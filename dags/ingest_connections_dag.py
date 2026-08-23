"""Connection Lens — LinkedIn export ingestion DAG.

The DAG is **trigger-agnostic** (§5, §8, §17). Whichever of the three modes
started it — the Airflow UI, a MinIO bucket event, or the Streamlit Job
Management button — it does exactly the same thing:

1. scan the MinIO landing zone for content hashes not yet in Bronze,
2. ingest what is genuinely new (re-checking the full MD5 against Bronze),
3. validate the landed batch with Great Expectations,
4. run dbt: Silver -> SCD2 snapshot -> Gold/marts -> tests.

`triggered_by` is read only to write it into the logs; it never influences
control flow. `max_active_runs=1` keeps DuckDB's single writer safe when
several triggers fire at once (§5, §10).
"""

from __future__ import annotations

import logging
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from airflow.decorators import dag, task, task_group
from airflow.models.param import Param

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.airflow_client import extract_triggered_by  # noqa: E402
from common.bronze import ingest_pending_objects, scan_for_pending_objects  # noqa: E402
from common.data_quality import (  # noqa: E402
    fetch_snapshot_frame,
    run_bronze_to_silver_checkpoint,
)
from common.duckdb_io import connect_read_only, connect_read_write  # noqa: E402
from common.minio_client import LandingZoneClient  # noqa: E402
from common.models import IngestionReport, LandingObject  # noqa: E402
from common.settings import get_settings  # noqa: E402

logger = logging.getLogger(__name__)

DAG_ID = "ingest_connections"

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "connection_lens",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=30),
    "depends_on_past": False,
}


def _dbt_command(subcommand: str) -> str:
    """Build a fully self-contained dbt CLI call.

    Settings are read when the task *runs*, never at DAG parse time. dbt's
    target/log paths are redirected to /tmp so dbt never needs write access to
    the bind-mounted project directory.
    """
    settings = get_settings()
    environment = {
        "DUCKDB_PATH": str(settings.duckdb_file),
        "DBT_TARGET": settings.dbt_target,
        "DBT_TARGET_PATH": "/tmp/dbt_target",
        "DBT_LOG_PATH": "/tmp/dbt_logs",
    }
    exported = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in environment.items()
    )
    return (
        f"env {exported} dbt {subcommand}"
        f" --project-dir {shlex.quote(str(settings.dbt_project_path))}"
        f" --profiles-dir {shlex.quote(str(settings.dbt_profiles_path))}"
    )


@dag(
    dag_id=DAG_ID,
    description="Ingest LinkedIn connection exports from MinIO into DuckDB, then run dbt.",
    doc_md=__doc__,
    # Never on a cron: this DAG only ever runs because a human or a bucket
    # event asked for it (§3, §8).
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    # DuckDB is single-writer; serialise runs no matter how many triggers fire.
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args=DEFAULT_ARGS,
    tags=["connection_lens", "bronze", "dbt", "duckdb"],
    params={
        "force_transform": Param(
            False,
            type="boolean",
            title="Run dbt even when no new export was ingested",
            description=(
                "Off by default: with nothing new in the landing zone the run "
                "is a deliberate no-op. Turn on to rebuild Silver/Gold from "
                "the Bronze data already present."
            ),
        )
    },
)
def ingest_connections() -> None:
    """Landing zone -> Bronze -> data quality -> dbt Silver/Gold/marts."""

    @task
    def log_trigger_source(**context: Any) -> str:
        """Record *why* this run happened — for observability only (§5, §17)."""
        dag_run = context["dag_run"]
        source = extract_triggered_by(getattr(dag_run, "conf", None) or {})
        logger.info(
            "DAG run %s triggered by: %s. This value is logged only — the "
            "ingestion logic is identical for every trigger mode.",
            dag_run.run_id,
            source,
        )
        return source

    @task
    def scan_landing_zone() -> list[dict[str, Any]]:
        """List MinIO objects whose content hash is not yet in Bronze."""
        settings = get_settings()
        client = LandingZoneClient.from_settings(settings)
        client.ensure_bucket()
        with connect_read_write(settings.duckdb_file) as connection:
            pending = scan_for_pending_objects(client, connection)
        if not pending:
            logger.info(
                "Nothing pending: every object in the landing zone is already "
                "in Bronze. This run is a no-op by design."
            )
        return [obj.model_dump(mode="json") for obj in pending]

    @task
    def ingest_new_objects_to_bronze(pending: list[dict[str, Any]]) -> dict[str, Any]:
        """Append genuinely new exports to Bronze, skipping duplicates loudly."""
        settings = get_settings()
        objects = [LandingObject.model_validate(item) for item in pending]
        if not objects:
            report = IngestionReport()
            logger.info("No candidate objects to ingest — %s", report.summary_line)
            return report.model_dump(mode="json")

        client = LandingZoneClient.from_settings(settings)
        with connect_read_write(settings.duckdb_file) as connection:
            report = ingest_pending_objects(connection, client, objects)
        for result in report.results:
            logger.info(result.message)
        return report.model_dump(mode="json")

    @task.short_circuit(ignore_downstream_trigger_rules=True)
    def has_new_data(report: dict[str, Any], **context: Any) -> bool:
        """Stop the run when nothing new landed (§14 scenarios 2, 7, 9, 10)."""
        ingestion_report = IngestionReport.model_validate(report)
        forced = bool(context["params"].get("force_transform"))
        if ingestion_report.has_new_data:
            logger.info("New data ingested — %s", ingestion_report.summary_line)
            return True
        if forced:
            logger.info(
                "No new data (%s) but force_transform=true — rebuilding from "
                "existing Bronze.",
                ingestion_report.summary_line,
            )
            return True
        logger.info(
            "No new data (%s). Downstream transformation is skipped: a "
            "redundant trigger is a safe no-op.",
            ingestion_report.summary_line,
        )
        return False

    @task
    def validate_bronze_batch() -> dict[str, Any]:
        """Great Expectations checkpoint between Bronze and Silver (§12)."""
        settings = get_settings()
        with connect_read_only(settings.duckdb_file) as connection:
            frame = fetch_snapshot_frame(connection)
        quality_report = run_bronze_to_silver_checkpoint(frame)
        logger.info(quality_report.summary_line)
        if not quality_report.success:
            raise ValueError(
                "Bronze -> Silver data quality checkpoint failed: "
                + ", ".join(quality_report.failed_expectations)
            )
        return quality_report.model_dump(mode="json")

    @task_group(group_id="transform_with_dbt")
    def transform_with_dbt() -> None:
        """Silver -> SCD2 snapshot -> Gold/marts -> dbt tests."""

        @task.bash
        def run_dbt_silver() -> str:
            return _dbt_command("run --select path:models/staging")

        @task.bash
        def run_dbt_snapshot_dim_connection() -> str:
            # SCD2 with hard_deletes='invalidate', fed only the latest Silver
            # snapshot — both are configured in the snapshot itself (§5, §18).
            return _dbt_command("snapshot")

        @task.bash
        def run_dbt_gold_marts() -> str:
            return _dbt_command("run --select path:models/marts")

        @task.bash
        def run_dbt_tests() -> str:
            return _dbt_command("test")

        @task.bash
        def check_source_freshness() -> str:
            # Stands in for Elementary's freshness monitor (§12).
            return _dbt_command("source freshness")

        (
            run_dbt_silver()
            >> run_dbt_snapshot_dim_connection()
            >> run_dbt_gold_marts()
            >> run_dbt_tests()
            >> check_source_freshness()
        )

    @task
    def log_ingestion_summary(report: dict[str, Any]) -> None:
        """Audit trail of what this run changed — logged even on a no-op run."""
        ingestion_report = IngestionReport.model_validate(report)
        logger.info("Ingestion summary: %s", ingestion_report.summary_line)
        for result in ingestion_report.results:
            logger.info("  %s -> %s", result.key, result.status)

    trigger_source = log_trigger_source()
    pending_objects = scan_landing_zone()
    ingestion_report = ingest_new_objects_to_bronze(pending_objects)

    # Deliberately outside the short-circuit branch: a no-op run must still
    # say clearly in its logs that it skipped duplicates and why (§17).
    log_ingestion_summary(ingestion_report)

    gate = has_new_data(ingestion_report)
    trigger_source >> pending_objects
    gate >> validate_bronze_batch() >> transform_with_dbt()


ingest_connections()
