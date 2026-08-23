"""Landing-zone object key conventions.

Keys look like::

    raw/linkedin_connections/20260823T140501Z_1f3c9ab2.csv
    <----- prefix --------->  <-- snapshot_ts --> <hash8>

The key is the only place the snapshot timestamp is recorded, so Bronze can
recover it later. An unparsable key is an error, never a guess (§17).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from common.errors import ObjectKeyError
from common.hashing import SHORT_HASH_LENGTH, short_hash

SNAPSHOT_TS_FORMAT = "%Y%m%dT%H%M%SZ"

_KEY_PATTERN = re.compile(
    rf"^(?P<prefix>.*/)?(?P<snapshot_ts>\d{{8}}T\d{{6}}Z)"
    rf"_(?P<hash8>[0-9a-f]{{{SHORT_HASH_LENGTH}}})\.csv$"
)


@dataclass(frozen=True)
class ObjectKeyParts:
    """The information encoded in a landing-zone object key."""

    key: str
    snapshot_ts: datetime
    hash8: str


def utcnow() -> datetime:
    """Current UTC time, truncated to whole seconds (the key's resolution)."""
    return datetime.now(UTC).replace(microsecond=0)


def build_object_key(prefix: str, snapshot_ts: datetime, file_hash: str) -> str:
    """Build the MinIO key for an uploaded export."""
    if snapshot_ts.tzinfo is not None:
        snapshot_ts = snapshot_ts.astimezone(UTC)
    stamp = snapshot_ts.strftime(SNAPSHOT_TS_FORMAT)
    return f"{prefix.strip('/')}/{stamp}_{short_hash(file_hash)}.csv"


def parse_object_key(key: str) -> ObjectKeyParts:
    """Recover ``snapshot_ts`` and the short hash from an object key."""
    match = _KEY_PATTERN.match(key)
    if match is None:
        raise ObjectKeyError(
            f"Object key {key!r} does not follow the landing-zone convention "
            "'<prefix>/<YYYYMMDDTHHMMSSZ>_<hash8>.csv'."
        )
    snapshot_ts = datetime.strptime(match.group("snapshot_ts"), SNAPSHOT_TS_FORMAT)
    return ObjectKeyParts(
        key=key,
        snapshot_ts=snapshot_ts.replace(tzinfo=UTC),
        hash8=match.group("hash8"),
    )
