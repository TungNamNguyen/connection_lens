"""The idempotency key is the MD5 of the file's bytes — nothing else (§5)."""

from __future__ import annotations

import hashlib

import pytest

from common.hashing import md5_bytes, md5_file, md5_stream, short_hash


def test_md5_bytes_matches_hashlib(export_v1: bytes) -> None:
    assert md5_bytes(export_v1) == hashlib.md5(export_v1).hexdigest()


def test_identical_content_hashes_identically(export_v1: bytes) -> None:
    assert md5_bytes(export_v1) == md5_bytes(bytes(export_v1))


def test_different_content_hashes_differently(export_v1: bytes, export_v2: bytes) -> None:
    assert md5_bytes(export_v1) != md5_bytes(export_v2)


def test_a_single_byte_change_changes_the_hash(export_v1: bytes) -> None:
    mutated = export_v1.replace(b"Data Analyst", b"Data Analyst ")
    assert mutated != export_v1
    assert md5_bytes(mutated) != md5_bytes(export_v1)


def test_md5_file_and_stream_agree(tmp_path, export_v1: bytes) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(export_v1)
    with path.open("rb") as handle:
        assert md5_stream(handle) == md5_file(path) == md5_bytes(export_v1)


def test_short_hash_takes_the_prefix() -> None:
    assert short_hash("0123456789abcdef") == "01234567"


def test_short_hash_rejects_a_too_short_value() -> None:
    with pytest.raises(ValueError):
        short_hash("abc")
