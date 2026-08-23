"""DuckDB access helpers.

DuckDB is single-writer / multi-reader (§10):

* :func:`connect_read_write` is used **only** by the Airflow DAG process.
* :func:`connect_read_only` is what Streamlit uses, always.

Bronze is append-only and stores the export's columns as raw strings plus
ingestion metadata; cleaning and typing happen in Silver (§7).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from common.errors import WarehouseNotReadyError

logger = logging.getLogger(__name__)

BRONZE_SCHEMA = "bronze"
BRONZE_TABLE = "raw_connections"
BRONZE_RELATION = f"{BRONZE_SCHEMA}.{BRONZE_TABLE}"

#: Export columns (snake_cased) stored verbatim in Bronze.
SOURCE_COLUMNS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "url",
    "email_address",
    "company",
    "position",
    "connected_on",
)

#: Ingestion metadata appended to every Bronze row (§7).
METADATA_COLUMNS: tuple[str, ...] = (
    "snapshot_ts",
    "file_hash",
    "source_object",
    "source_row_number",
    "ingested_at",
)

BRONZE_COLUMNS: tuple[str, ...] = SOURCE_COLUMNS + METADATA_COLUMNS

_BRONZE_DDL = f"""
create schema if not exists {BRONZE_SCHEMA};
create table if not exists {BRONZE_RELATION} (
    first_name        varchar,
    last_name         varchar,
    url               varchar,
    email_address     varchar,
    company           varchar,
    position          varchar,
    connected_on      varchar,
    snapshot_ts       timestamp not null,
    file_hash         varchar not null,
    source_object     varchar not null,
    source_row_number integer not null,
    ingested_at       timestamp not null
);
"""


def as_naive_utc(value: datetime) -> datetime:
    """Normalise a datetime to naive UTC — every timestamp in the warehouse is UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@contextmanager
def connect_read_write(path: str | Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the single writer connection. Airflow DAG only (§10)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(target), read_only=False)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def connect_read_only(path: str | Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a read-only connection. Everything in Streamlit uses this (§10)."""
    target = Path(path)
    if not target.exists():
        raise WarehouseNotReadyError(
            f"No warehouse at {target}. Upload an export and run the ingestion "
            "DAG before querying."
        )
    connection = duckdb.connect(str(target), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def ensure_bronze(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the Bronze schema/table when missing."""
    connection.execute(_BRONZE_DDL)


def bronze_exists(connection: duckdb.DuckDBPyConnection) -> bool:
    """Return whether the Bronze table has been created yet."""
    found = connection.execute(
        """
        select count(*)
        from information_schema.tables
        where table_schema = ? and table_name = ?
        """,
        [BRONZE_SCHEMA, BRONZE_TABLE],
    ).fetchone()
    return bool(found and found[0])


def fetch_ingested_hashes(connection: duckdb.DuckDBPyConnection) -> set[str]:
    """Return every ``file_hash`` already present in Bronze — the dataset of record (§5)."""
    if not bronze_exists(connection):
        return set()
    rows = connection.execute(
        f"select distinct file_hash from {BRONZE_RELATION}"
    ).fetchall()
    return {row[0] for row in rows}


def hash_in_bronze(connection: duckdb.DuckDBPyConnection, file_hash: str) -> bool:
    """Return whether this exact content has already been ingested."""
    if not bronze_exists(connection):
        return False
    found = connection.execute(
        f"select 1 from {BRONZE_RELATION} where file_hash = ? limit 1", [file_hash]
    ).fetchone()
    return found is not None


def append_bronze_batch(
    connection: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    *,
    snapshot_ts: datetime,
    file_hash: str,
    source_object: str,
    ingested_at: datetime | None = None,
) -> int:
    """Append one parsed export to Bronze and return the row count written.

    Bronze is append-only: no upsert, no de-duplication of people. Comparing
    snapshots is Gold's job (§17).
    """
    ensure_bronze(connection)

    batch = frame.copy()
    for column in SOURCE_COLUMNS:
        if column not in batch.columns:
            logger.warning(
                "Optional column %r absent from %s — storing it as empty in Bronze.",
                column,
                source_object,
            )
            batch[column] = ""
    batch = batch[list(SOURCE_COLUMNS)]
    batch["snapshot_ts"] = as_naive_utc(snapshot_ts)
    batch["file_hash"] = file_hash
    batch["source_object"] = source_object
    batch["source_row_number"] = range(1, len(batch) + 1)
    batch["ingested_at"] = as_naive_utc(ingested_at or datetime.now(UTC))

    column_list = ", ".join(BRONZE_COLUMNS)
    connection.register("incoming_batch", batch)
    try:
        connection.execute(
            f"insert into {BRONZE_RELATION} ({column_list}) "
            f"select {column_list} from incoming_batch"
        )
    finally:
        connection.unregister("incoming_batch")
    return len(batch)
