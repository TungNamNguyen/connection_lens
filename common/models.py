"""Typed objects passed between layers (§11).

These are deliberately plain data carriers: every one of them round-trips
through ``model_dump(mode="json")`` so it can cross an Airflow XCom boundary
without a custom serializer.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TriggerSource(StrEnum):
    """Value written to ``dag_run.conf['triggered_by']`` (§5, §8)."""

    STREAMLIT = "streamlit"
    MINIO_EVENT = "minio_event"
    MANUAL_UI = "manual_ui"

    @property
    def label(self) -> str:
        return {
            TriggerSource.STREAMLIT: "Streamlit",
            TriggerSource.MINIO_EVENT: "MinIO event",
            TriggerSource.MANUAL_UI: "manual (Airflow UI)",
        }[self]


class LandingObject(BaseModel):
    """An object sitting in the MinIO landing zone."""

    model_config = ConfigDict(frozen=True)

    key: str
    snapshot_ts: datetime
    hash8: str
    size_bytes: int = 0
    last_modified: datetime | None = None


class ObjectIngestionResult(BaseModel):
    """What happened to a single landing-zone object during a DAG run."""

    key: str
    snapshot_ts: datetime
    file_hash: str
    status: Literal["ingested", "skipped_duplicate"]
    rows_ingested: int = 0
    message: str = ""


class IngestionReport(BaseModel):
    """Aggregate outcome of one ingestion task run."""

    objects_scanned: int = 0
    objects_ingested: int = 0
    objects_skipped_duplicate: int = 0
    rows_ingested: int = 0
    results: list[ObjectIngestionResult] = Field(default_factory=list)

    def add(self, result: ObjectIngestionResult) -> None:
        self.results.append(result)
        if result.status == "ingested":
            self.objects_ingested += 1
            self.rows_ingested += result.rows_ingested
        else:
            self.objects_skipped_duplicate += 1

    @property
    def has_new_data(self) -> bool:
        return self.objects_ingested > 0

    @property
    def summary_line(self) -> str:
        return (
            f"scanned={self.objects_scanned} ingested={self.objects_ingested} "
            f"skipped_duplicate={self.objects_skipped_duplicate} "
            f"rows_ingested={self.rows_ingested}"
        )


class UploadResult(BaseModel):
    """Outcome of the Streamlit Upload tab handing a file to MinIO (§7)."""

    object_key: str
    file_hash: str
    snapshot_ts: datetime
    row_count: int
    is_duplicate_of_bronze: bool
    message: str


class DagRunSummary(BaseModel):
    """One row of the Job Management run-history table (§9)."""

    dag_run_id: str
    state: str
    triggered_by: str
    logical_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    note: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.start_date is None or self.end_date is None:
            return None
        return (self.end_date - self.start_date).total_seconds()


class TaskInstanceSummary(BaseModel):
    """A task instance inside a DAG run, used to fetch logs (§9)."""

    task_id: str
    state: str | None = None
    try_number: int = 1
