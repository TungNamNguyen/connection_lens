"""Landing-zone object key conventions (§7)."""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from common.errors import ObjectKeyError
from common.naming import build_object_key, parse_object_key

PREFIX = "raw/linkedin_connections"
SNAPSHOT_TS = datetime(2026, 8, 23, 14, 5, 1, tzinfo=UTC)
FILE_HASH = "1f3c9ab2c4d5e6f708192a3b4c5d6e7f"


def test_build_object_key_uses_the_documented_layout() -> None:
    key = build_object_key(PREFIX, SNAPSHOT_TS, FILE_HASH)
    assert key == f"{PREFIX}/20260823T140501Z_1f3c9ab2.csv"


def test_build_object_key_normalises_to_utc() -> None:
    from datetime import timedelta

    local = SNAPSHOT_TS.astimezone(timezone(timedelta(hours=7)))
    assert build_object_key(PREFIX, local, FILE_HASH) == build_object_key(
        PREFIX, SNAPSHOT_TS, FILE_HASH
    )


def test_key_round_trips() -> None:
    key = build_object_key(PREFIX, SNAPSHOT_TS, FILE_HASH)
    parts = parse_object_key(key)
    assert parts.snapshot_ts == SNAPSHOT_TS
    assert parts.hash8 == FILE_HASH[:8]


@pytest.mark.parametrize(
    "key",
    [
        "raw/linkedin_connections/not-a-timestamp_1f3c9ab2.csv",
        "raw/linkedin_connections/20260823T140501Z.csv",
        "raw/linkedin_connections/20260823T140501Z_1f3c9ab2.txt",
        "raw/linkedin_connections/20260823T140501Z_XYZ.csv",
    ],
)
def test_unparsable_keys_fail_loudly(key: str) -> None:
    with pytest.raises(ObjectKeyError):
        parse_object_key(key)
