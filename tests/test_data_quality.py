"""Great Expectations checkpoint between Bronze and Silver (§12)."""

from __future__ import annotations

import pandas as pd
import pytest

from common.bronze import ingest_object
from common.data_quality import (
    count_restricted_profile_rows,
    fetch_snapshot_frame,
    run_bronze_to_silver_checkpoint,
)
from common.hashing import md5_bytes
from tests.conftest import BASE_SNAPSHOT_TS


@pytest.fixture
def bronze_frame(connection, landing_zone, export_v1: bytes) -> pd.DataFrame:
    landed = landing_zone.put_export(export_v1, md5_bytes(export_v1), BASE_SNAPSHOT_TS)
    ingest_object(connection, landed, export_v1)
    return fetch_snapshot_frame(connection)


def test_a_healthy_export_passes(bronze_frame: pd.DataFrame) -> None:
    report = run_bronze_to_silver_checkpoint(bronze_frame)
    assert report.success, report.failed_expectations
    assert report.row_count == 11


def test_blank_emails_never_fail_the_checkpoint(bronze_frame: pd.DataFrame) -> None:
    """Email is opt-in; an all-blank column is correct, not a defect (§5, §12)."""
    frame = bronze_frame.copy()
    frame["email_address"] = ""
    assert run_bronze_to_silver_checkpoint(frame).success


def test_restricted_profile_rows_are_counted_not_hidden(
    bronze_frame: pd.DataFrame,
) -> None:
    assert count_restricted_profile_rows(bronze_frame) == 1
    report = run_bronze_to_silver_checkpoint(bronze_frame)
    assert report.restricted_profile_rows == 1


def test_a_schema_change_fails_loudly(bronze_frame: pd.DataFrame) -> None:
    frame = bronze_frame.copy()
    frame["mystery_score"] = 1
    report = run_bronze_to_silver_checkpoint(frame)
    assert not report.success
    assert "expect_table_columns_to_match_set" in report.failed_expectations


def test_a_changed_date_format_fails(bronze_frame: pd.DataFrame) -> None:
    frame = bronze_frame.copy()
    frame["connected_on"] = "2025-02-04"
    report = run_bronze_to_silver_checkpoint(frame)
    assert not report.success
    assert "expect_column_values_to_match_regex" in report.failed_expectations


def test_mostly_blank_urls_fail(bronze_frame: pd.DataFrame) -> None:
    """A handful of restricted profiles is fine; half the file is not."""
    frame = bronze_frame.copy()
    frame.loc[frame.index[:6], "url"] = ""
    assert not run_bronze_to_silver_checkpoint(frame).success


def test_an_empty_batch_fails(bronze_frame: pd.DataFrame) -> None:
    assert not run_bronze_to_silver_checkpoint(bronze_frame.iloc[0:0]).success
