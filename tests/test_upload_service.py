"""Upload tab flow: validate -> hash -> check Bronze -> land in MinIO (§7)."""

from __future__ import annotations

import pytest

from common.errors import CsvSchemaError
from common.hashing import md5_bytes
from streamlit_app.upload_service import perform_upload, prepare_upload


def test_prepare_upload_validates_and_hashes(export_v1: bytes) -> None:
    prepared = prepare_upload(export_v1)
    assert prepared.file_hash == md5_bytes(export_v1)
    assert prepared.row_count == 11
    assert prepared.parsed.header_line_index == 3


def test_a_broken_file_is_rejected_before_upload(
    export_missing_column: bytes, landing_zone
) -> None:
    """§14 scenario 6: nothing is hashed or uploaded when validation fails."""
    with pytest.raises(CsvSchemaError):
        prepare_upload(export_missing_column)
    assert landing_zone.put_calls == []


def test_new_content_lands_under_the_documented_key(export_v1: bytes, landing_zone) -> None:
    prepared = prepare_upload(export_v1)
    result = perform_upload(prepared, landing_zone, lambda _: False)

    assert not result.is_duplicate_of_bronze
    assert result.object_key.startswith("raw/linkedin_connections/")
    assert result.object_key.endswith(f"_{prepared.file_hash[:8]}.csv")
    assert "Trigger the ingestion DAG" in result.message


def test_duplicate_content_is_still_uploaded_for_the_audit_trail(
    export_v1: bytes, landing_zone
) -> None:
    """MinIO keeps duplicates on purpose; only Bronze de-duplicates (§5)."""
    prepared = prepare_upload(export_v1)
    result = perform_upload(prepared, landing_zone, lambda _: True)

    assert result.is_duplicate_of_bronze
    assert len(landing_zone.put_calls) == 1
    assert "no new dataset will be created" in result.message


def test_upload_never_triggers_ingestion(export_v1: bytes, landing_zone) -> None:
    """The Upload tab lands files only — triggering lives in Job Management (§7)."""
    prepared = prepare_upload(export_v1)
    result = perform_upload(prepared, landing_zone, lambda _: False)
    assert "Job Management" in result.message or "Trigger the ingestion DAG" in result.message
