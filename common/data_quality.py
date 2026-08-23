"""Great Expectations checkpoint between Bronze and Silver (§12).

Run by the Airflow DAG right after ingestion and before dbt: if the landed
data does not look like a LinkedIn export any more, the transformation layer
should never see it.

Deliberate omission: there is **no** not-null expectation on
``email_address``. LinkedIn only exports an email when the connection opted
in, so most rows are legitimately blank (§5, §12, §16, §17).

Great Expectations is imported lazily so the Streamlit image does not need it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import BaseModel, Field

from common.duckdb_io import BRONZE_COLUMNS, BRONZE_RELATION

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

logger = logging.getLogger(__name__)

SUITE_NAME = "bronze_to_silver"

#: A LinkedIn profile URL, percent-encoding allowed.
LINKEDIN_URL_REGEX = r"^https://(?:www\.)?linkedin\.com/in/[^\s]+$"

#: The export's own date format, e.g. "17 Aug 2026" (§16).
CONNECTED_ON_REGEX = r"^\d{1,2} [A-Za-z]{3} \d{4}$"

#: Share of rows that must carry a usable profile URL. Restricted/deactivated
#: profiles export as a date-only row, so a small share is normal — a large
#: share means the export changed shape. See docs/data_quality.md.
MIN_IDENTIFIABLE_SHARE = 0.90

#: Columns that are together blank on a "restricted profile" row.
IDENTITY_COLUMNS = ("url", "first_name", "last_name", "company", "position")


class DataQualityReport(BaseModel):
    """Outcome of one checkpoint run, safe to push through an XCom."""

    success: bool
    row_count: int
    evaluated_expectations: int = 0
    successful_expectations: int = 0
    failed_expectations: list[str] = Field(default_factory=list)
    restricted_profile_rows: int = 0

    @property
    def summary_line(self) -> str:
        status = "PASSED" if self.success else "FAILED"
        return (
            f"Great Expectations {SUITE_NAME} {status}: "
            f"{self.successful_expectations}/{self.evaluated_expectations} "
            f"expectations met over {self.row_count} row(s); "
            f"{self.restricted_profile_rows} restricted-profile row(s)."
        )


def count_restricted_profile_rows(frame: pd.DataFrame) -> int:
    """Count rows where every identity column is blank (restricted profiles)."""
    present = [column for column in IDENTITY_COLUMNS if column in frame.columns]
    if not present:
        return 0
    blank = frame[present].fillna("").apply(lambda column: column.str.strip() == "")
    return int(blank.all(axis=1).sum())


def fetch_snapshot_frame(
    connection: duckdb.DuckDBPyConnection, snapshot_ts: Any = None
) -> pd.DataFrame:
    """Read one Bronze snapshot (default: the latest) into a DataFrame."""
    columns = ", ".join(BRONZE_COLUMNS)
    if snapshot_ts is None:
        query = (
            f"select {columns} from {BRONZE_RELATION} "
            f"where snapshot_ts = (select max(snapshot_ts) from {BRONZE_RELATION})"
        )
        return connection.execute(query).df()
    query = f"select {columns} from {BRONZE_RELATION} where snapshot_ts = ?"
    return connection.execute(query, [snapshot_ts]).df()


def build_expectations() -> list[Any]:
    """Build the Bronze -> Silver expectation list.

    Kept as its own function so the suite can be asserted in unit tests
    without running a checkpoint.
    """
    from great_expectations import expectations as gxe

    return [
        # Schema evolution must fail loudly, never be silently coerced (§17).
        gxe.ExpectTableColumnsToMatchSet(
            column_set=list(BRONZE_COLUMNS), exact_match=True
        ),
        gxe.ExpectTableRowCountToBeBetween(min_value=1),
        # Ingestion metadata integrity.
        gxe.ExpectColumnValuesToNotBeNull(column="file_hash"),
        gxe.ExpectColumnValuesToNotBeNull(column="snapshot_ts"),
        gxe.ExpectColumnValuesToNotBeNull(column="source_object"),
        gxe.ExpectColumnValuesToNotBeNull(column="ingested_at"),
        gxe.ExpectColumnValueLengthsToEqual(column="file_hash", value=32),
        # Identity: most rows must carry a parseable LinkedIn profile URL.
        gxe.ExpectColumnValuesToMatchRegex(
            column="url",
            regex=LINKEDIN_URL_REGEX,
            mostly=MIN_IDENTIFIABLE_SHARE,
        ),
        # The export's date format is a schema contract too.
        gxe.ExpectColumnValuesToMatchRegex(
            column="connected_on", regex=CONNECTED_ON_REGEX, mostly=1.0
        ),
        # NOTE: no expectation on `email_address` — blank is correct (§12).
    ]


def run_bronze_to_silver_checkpoint(frame: pd.DataFrame) -> DataQualityReport:
    """Validate a Bronze batch and return a typed report."""
    import great_expectations as gx

    context = gx.get_context(mode="ephemeral")
    try:  # pragma: no cover - cosmetic only
        context.variables.progress_bars = gx.data_context.types.base.ProgressBarsConfig(
            globally=False
        )
    except Exception:
        logger.debug("Could not disable Great Expectations progress bars.")

    data_source = context.data_sources.add_pandas("bronze")
    asset = data_source.add_dataframe_asset("raw_connections")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_batch")

    suite = context.suites.add(gx.ExpectationSuite(name=SUITE_NAME))
    for expectation in build_expectations():
        suite.add_expectation(expectation)

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name=f"{SUITE_NAME}_validation", data=batch_definition, suite=suite
        )
    )
    checkpoint = context.checkpoints.add(
        gx.Checkpoint(
            name=f"{SUITE_NAME}_checkpoint",
            validation_definitions=[validation_definition],
        )
    )
    result = checkpoint.run(batch_parameters={"dataframe": frame})

    validation_result = next(iter(result.run_results.values()))
    statistics = validation_result["statistics"]
    failed = [
        item["expectation_config"]["type"]
        for item in validation_result["results"]
        if not item["success"]
    ]
    report = DataQualityReport(
        success=bool(result.success),
        row_count=len(frame),
        evaluated_expectations=int(statistics["evaluated_expectations"]),
        successful_expectations=int(statistics["successful_expectations"]),
        failed_expectations=failed,
        restricted_profile_rows=count_restricted_profile_rows(frame),
    )
    logger.info(report.summary_line)
    return report
