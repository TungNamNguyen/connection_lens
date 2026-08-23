"""Bronze ingestion and idempotency — the scenario table in §14.

Idempotency is decided by content hash against Bronze, never by calendar date,
upload timestamp or which trigger fired the DAG (§5, §17).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.bronze import (
    ingest_object,
    ingest_pending_objects,
    scan_for_pending_objects,
    select_candidate_objects,
)
from common.duckdb_io import BRONZE_RELATION, fetch_ingested_hashes, hash_in_bronze
from common.errors import IngestionError
from common.hashing import md5_bytes
from common.models import LandingObject
from tests.conftest import BASE_SNAPSHOT_TS, FakeLandingZoneClient


def row_count(connection) -> int:
    return connection.execute(f"select count(*) from {BRONZE_RELATION}").fetchone()[0]


# --- §14 scenario 1: first-ever upload ------------------------------------
def test_first_ingestion_lands_every_row(connection, landing_zone, export_v1) -> None:
    landed = landing_zone.put_export(export_v1, md5_bytes(export_v1), BASE_SNAPSHOT_TS)
    result = ingest_object(connection, landed, export_v1)

    assert result.status == "ingested"
    assert result.rows_ingested == 11
    assert row_count(connection) == 11


def test_ingestion_records_provenance_metadata(connection, landing_zone, export_v1) -> None:
    file_hash = md5_bytes(export_v1)
    landed = landing_zone.put_export(export_v1, file_hash, BASE_SNAPSHOT_TS)
    ingest_object(connection, landed, export_v1)

    stored = connection.execute(
        f"""
        select distinct file_hash, source_object, snapshot_ts
        from {BRONZE_RELATION}
        """
    ).fetchall()
    assert len(stored) == 1
    assert stored[0][0] == file_hash
    assert stored[0][1] == landed.key
    # snapshot_ts comes from the object key, not from "now".
    assert stored[0][2] == BASE_SNAPSHOT_TS.replace(tzinfo=None)


# --- §14 scenario 2: re-upload of the exact same file ---------------------
def test_identical_content_is_skipped_on_reingestion(
    connection, landing_zone, export_v1
) -> None:
    file_hash = md5_bytes(export_v1)
    first = landing_zone.put_export(export_v1, file_hash, BASE_SNAPSHOT_TS)
    ingest_object(connection, first, export_v1)

    # Same bytes, uploaded again hours later under a different key.
    second = landing_zone.put_export(
        export_v1, file_hash, BASE_SNAPSHOT_TS + timedelta(hours=6)
    )
    result = ingest_object(connection, second, export_v1)

    assert result.status == "skipped_duplicate"
    assert result.rows_ingested == 0
    assert row_count(connection) == 11
    assert "SKIP duplicate content" in result.message


# --- §14 scenario 3/4/5: genuinely different content ----------------------
def test_different_content_is_appended_as_a_new_batch(
    connection, landed_exports, export_v1, export_v2
) -> None:
    pending = scan_for_pending_objects(landed_exports, connection)
    report = ingest_pending_objects(connection, landed_exports, pending)

    assert report.objects_ingested == 2
    assert report.rows_ingested == 23
    assert len(fetch_ingested_hashes(connection)) == 2
    # Bronze is append-only: both snapshots coexist, nothing is overwritten.
    snapshots = connection.execute(
        f"select count(distinct snapshot_ts) from {BRONZE_RELATION}"
    ).fetchone()[0]
    assert snapshots == 2


def test_same_calendar_day_different_content_both_land(
    connection, landing_zone, export_v1, export_v2
) -> None:
    """Idempotency keys on content, never on the calendar day (§17)."""
    morning = landing_zone.put_export(
        export_v1, md5_bytes(export_v1), BASE_SNAPSHOT_TS
    )
    afternoon = landing_zone.put_export(
        export_v2, md5_bytes(export_v2), BASE_SNAPSHOT_TS + timedelta(hours=4)
    )
    ingest_object(connection, morning, export_v1)
    ingest_object(connection, afternoon, export_v2)

    assert row_count(connection) == 23


# --- §14 scenario 7/9/10: redundant triggers ------------------------------
def test_a_second_identical_dag_run_is_a_no_op(
    connection, landed_exports
) -> None:
    first_pending = scan_for_pending_objects(landed_exports, connection)
    ingest_pending_objects(connection, landed_exports, first_pending)
    rows_after_first_run = row_count(connection)

    second_pending = scan_for_pending_objects(landed_exports, connection)
    second_report = ingest_pending_objects(connection, landed_exports, second_pending)

    assert second_pending == []
    assert second_report.objects_ingested == 0
    assert row_count(connection) == rows_after_first_run


def test_scan_returns_nothing_when_the_landing_zone_is_empty(connection) -> None:
    assert scan_for_pending_objects(FakeLandingZoneClient(), connection) == []


# --- Defence in depth ------------------------------------------------------
def test_full_hash_is_rechecked_even_if_the_scan_says_pending(
    connection, landing_zone, export_v1
) -> None:
    """The app layer is not trusted: Bronze itself decides (§7)."""
    file_hash = md5_bytes(export_v1)
    landed = landing_zone.put_export(export_v1, file_hash, BASE_SNAPSHOT_TS)
    ingest_object(connection, landed, export_v1)
    assert hash_in_bronze(connection, file_hash)

    forced = LandingObject(
        key=landed.key, snapshot_ts=landed.snapshot_ts, hash8=file_hash[:8]
    )
    result = ingest_object(connection, forced, export_v1)
    assert result.status == "skipped_duplicate"


def test_content_that_does_not_match_its_key_is_rejected(
    connection, landing_zone, export_v1, export_v2
) -> None:
    landed = landing_zone.put_export(export_v1, md5_bytes(export_v1), BASE_SNAPSHOT_TS)
    with pytest.raises(IngestionError, match="Refusing to ingest"):
        ingest_object(connection, landed, export_v2)


def test_candidate_selection_uses_the_short_hash_prefix() -> None:
    landing = [
        LandingObject(key="a", snapshot_ts=BASE_SNAPSHOT_TS, hash8="aaaaaaaa"),
        LandingObject(key="b", snapshot_ts=BASE_SNAPSHOT_TS, hash8="bbbbbbbb"),
    ]
    ingested = {"aaaaaaaa" + "0" * 24}
    assert [obj.key for obj in select_candidate_objects(landing, ingested)] == ["b"]


# --- Schema evolution ------------------------------------------------------
def test_a_broken_export_never_reaches_bronze(
    connection, landing_zone, export_missing_column
) -> None:
    from common.errors import CsvSchemaError

    file_hash = md5_bytes(export_missing_column)
    landed = landing_zone.put_export(
        export_missing_column, file_hash, BASE_SNAPSHOT_TS
    )
    with pytest.raises(CsvSchemaError):
        ingest_object(connection, landed, export_missing_column)
    assert row_count(connection) == 0
