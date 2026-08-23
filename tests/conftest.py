"""Shared pytest fixtures.

Every fixture here uses **synthetic** data only — no real export ever reaches
a test, a CI run or a commit (§1, §13, §18).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.duckdb_io import ensure_bronze  # noqa: E402
from common.hashing import md5_bytes  # noqa: E402
from common.models import LandingObject  # noqa: E402
from common.naming import build_object_key  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_PREFIX = "raw/linkedin_connections"
BASE_SNAPSHOT_TS = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)


def read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


@pytest.fixture
def export_v1() -> bytes:
    """Ten connections plus one restricted (URL-less) row; 3 note lines."""
    return read_fixture("connections_v1.csv")


@pytest.fixture
def export_v2() -> bytes:
    """One company change, one title change, one departure, two joiners; 4 note lines."""
    return read_fixture("connections_v2.csv")


@pytest.fixture
def export_missing_column() -> bytes:
    """An export without the required `Position` column."""
    return read_fixture("connections_missing_column.csv")


@pytest.fixture
def warehouse_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


@pytest.fixture
def connection(warehouse_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A writable throwaway warehouse with Bronze created."""
    con = duckdb.connect(str(warehouse_path))
    ensure_bronze(con)
    try:
        yield con
    finally:
        con.close()


class FakeLandingZoneClient:
    """In-memory stand-in for :class:`common.minio_client.LandingZoneClient`."""

    def __init__(self, raw_prefix: str = RAW_PREFIX) -> None:
        self.raw_prefix = raw_prefix
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    def ensure_bucket(self) -> None:  # pragma: no cover - trivial
        return None

    def is_reachable(self) -> bool:  # pragma: no cover - trivial
        return True

    def put_export(
        self, raw: bytes, file_hash: str, snapshot_ts: datetime | None = None
    ) -> LandingObject:
        snapshot_ts = snapshot_ts or BASE_SNAPSHOT_TS
        key = build_object_key(self.raw_prefix, snapshot_ts, file_hash)
        self.objects[key] = raw
        self.put_calls.append(key)
        return LandingObject(
            key=key,
            snapshot_ts=snapshot_ts,
            hash8=file_hash[:8],
            size_bytes=len(raw),
        )

    def list_landing_objects(self) -> list[LandingObject]:
        from common.naming import parse_object_key

        result = []
        for key, raw in self.objects.items():
            parts = parse_object_key(key)
            result.append(
                LandingObject(
                    key=key,
                    snapshot_ts=parts.snapshot_ts,
                    hash8=parts.hash8,
                    size_bytes=len(raw),
                )
            )
        return sorted(result, key=lambda obj: (obj.snapshot_ts, obj.key))

    def get_object_bytes(self, key: str) -> bytes:
        return self.objects[key]


@pytest.fixture
def landing_zone() -> FakeLandingZoneClient:
    return FakeLandingZoneClient()


@pytest.fixture
def landed_exports(
    landing_zone: FakeLandingZoneClient, export_v1: bytes, export_v2: bytes
) -> FakeLandingZoneClient:
    """Two synthetic exports already sitting in the landing zone."""
    landing_zone.put_export(export_v1, md5_bytes(export_v1), BASE_SNAPSHOT_TS)
    landing_zone.put_export(
        export_v2, md5_bytes(export_v2), BASE_SNAPSHOT_TS + timedelta(days=7)
    )
    return landing_zone
