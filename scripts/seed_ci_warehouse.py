#!/usr/bin/env python
"""Build a throwaway DuckDB warehouse from **synthetic** fixtures.

This is a CI / local-smoke-test helper, *not* an ingestion path: the real
pipeline always lands files in MinIO first and ingests them from the Airflow
DAG (§7, §17). It exists so `dbt build` can run in CI against a realistic
Bronze table without ever touching the owner's real export (§13).

Usage::

    python scripts/seed_ci_warehouse.py --duckdb-path /tmp/ci.duckdb --overwrite
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.csv_schema import parse_export  # noqa: E402
from common.duckdb_io import (  # noqa: E402
    append_bronze_batch,
    connect_read_write,
    ensure_bronze,
)
from common.hashing import md5_bytes  # noqa: E402
from common.naming import build_object_key  # noqa: E402

logger = logging.getLogger("seed_ci_warehouse")

DEFAULT_FIXTURES = (
    REPO_ROOT / "tests" / "fixtures" / "connections_v1.csv",
    REPO_ROOT / "tests" / "fixtures" / "connections_v2.csv",
)
BASE_SNAPSHOT_TS = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)


def snapshot_offset(fixture: Path, index: int) -> int:
    """Keep a fixture's snapshot_ts stable whether it is seeded alone or in a batch."""
    ordered = [path.name for path in DEFAULT_FIXTURES]
    if fixture.name in ordered:
        return ordered.index(fixture.name) * 7
    return index * 7


def seed(
    duckdb_path: Path, fixtures: list[Path], overwrite: bool, append: bool = False
) -> int:
    if duckdb_path.exists():
        if not (overwrite or append):
            raise SystemExit(
                f"{duckdb_path} already exists. Refusing to touch an existing "
                "warehouse — pass --overwrite (recreate) or --append (add one "
                "more synthetic snapshot) for a throwaway CI database."
            )
        if overwrite:
            duckdb_path.unlink()

    total_rows = 0
    with connect_read_write(duckdb_path) as connection:
        ensure_bronze(connection)
        for index, fixture in enumerate(fixtures):
            raw = fixture.read_bytes()
            file_hash = md5_bytes(raw)
            snapshot_ts = BASE_SNAPSHOT_TS + timedelta(days=snapshot_offset(fixture, index))
            parsed = parse_export(raw)
            rows = append_bronze_batch(
                connection,
                parsed.frame,
                snapshot_ts=snapshot_ts,
                file_hash=file_hash,
                source_object=build_object_key(
                    "raw/linkedin_connections", snapshot_ts, file_hash
                ),
            )
            total_rows += rows
            logger.info(
                "Seeded %s: %d row(s) at snapshot_ts=%s (md5=%s, header line %d)",
                fixture.name,
                rows,
                snapshot_ts.isoformat(),
                file_hash,
                parsed.header_line_index + 1,
            )
    return total_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb-path", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, action="append", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fixtures = args.fixture or list(DEFAULT_FIXTURES)
    rows = seed(args.duckdb_path.resolve(), fixtures, args.overwrite, args.append)
    logger.info("Done: %d Bronze row(s) in %s", rows, args.duckdb_path)


if __name__ == "__main__":
    main()
