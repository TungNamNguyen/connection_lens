"""Content hashing — the one and only idempotency key of this project (§5).

Idempotency is decided by the MD5 of the *raw file bytes*, checked against
Bronze. Never by calendar date, upload timestamp or trigger source (§14, §17).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

_CHUNK_SIZE = 1024 * 1024

SHORT_HASH_LENGTH = 8


def md5_bytes(data: bytes) -> str:
    """Return the hex MD5 digest of ``data``."""
    return hashlib.md5(data).hexdigest()


def md5_stream(stream: BinaryIO) -> str:
    """Return the hex MD5 digest of a binary stream, read in chunks."""
    digest = hashlib.md5()
    for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: str | Path) -> str:
    """Return the hex MD5 digest of a file on disk."""
    with Path(path).open("rb") as handle:
        return md5_stream(handle)


def short_hash(file_hash: str, length: int = SHORT_HASH_LENGTH) -> str:
    """Return the first ``length`` characters of a hash, for use in object keys."""
    if len(file_hash) < length:
        raise ValueError(f"hash {file_hash!r} is shorter than {length} characters")
    return file_hash[:length]
