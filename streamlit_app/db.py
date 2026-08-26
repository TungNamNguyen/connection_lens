"""Read-only warehouse access for the Streamlit app.

Every connection opened here is `read_only=True`. Streamlit never writes to
Bronze, Silver or Gold — all warehouse writes happen inside the Airflow DAG,
which is what keeps DuckDB's single-writer constraint safe (§10, §17).
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from common.duckdb_io import BRONZE_RELATION, connect_read_only
from common.errors import WarehouseNotReadyError
from common.settings import get_settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30

DIM_CONNECTION = "gold.dim_connection"
DIM_COMPANY = "gold.dim_company"
FCT_CONNECTION_SNAPSHOT = "gold.fct_connection_snapshot"
MART_NETWORK_STATS = "mart.mart_network_stats"
MART_NETWORK_BREAKDOWN = "mart.mart_network_breakdown"


def warehouse_path() -> Path:
    """Absolute path of the DuckDB file, from configuration."""
    return get_settings().duckdb_file


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a read-only query and return a DataFrame."""
    with connect_read_only(warehouse_path()) as connection:
        return connection.execute(sql, params or []).df()


def _empty() -> pd.DataFrame:
    return pd.DataFrame()


def safe_query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a query, returning an empty frame when the warehouse is not built yet.

    Missing relations are expected before the first DAG run; anything else is
    re-raised so real errors stay loud.
    """
    try:
        return query(sql, params)
    except WarehouseNotReadyError:
        return _empty()
    except duckdb.CatalogException as error:
        logger.info("Relation not available yet: %s", error)
        return _empty()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def warehouse_status() -> dict[str, bool]:
    """Which layers exist, for the app's status banners."""
    path = warehouse_path()
    if not path.exists():
        return {"warehouse": False, "bronze": False, "gold": False, "marts": False}
    with connect_read_only(path) as connection:
        rows = connection.execute(
            """
            select table_schema, table_name
            from information_schema.tables
            """
        ).fetchall()
    relations = {f"{schema}.{table}" for schema, table in rows}
    return {
        "warehouse": True,
        "bronze": BRONZE_RELATION in relations,
        "gold": DIM_CONNECTION in relations,
        "marts": MART_NETWORK_STATS in relations,
    }


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def bronze_file_hashes() -> set[str]:
    """Content hashes already ingested — the Upload tab's duplicate check (§7)."""
    frame = safe_query(f"select distinct file_hash from {BRONZE_RELATION}")
    if frame.empty:
        return set()
    return set(frame["file_hash"].tolist())


def is_hash_in_bronze(file_hash: str) -> bool:
    """Whether this exact file content has already been ingested."""
    return file_hash in bronze_file_hashes()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_ingestion_log() -> pd.DataFrame:
    """One row per ingested export: when, from which object, how many rows."""
    return safe_query(
        f"""
        select
            snapshot_ts,
            file_hash,
            source_object,
            count(*) as row_count,
            max(ingested_at) as ingested_at
        from {BRONZE_RELATION}
        group by snapshot_ts, file_hash, source_object
        order by snapshot_ts desc
        """
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_network_stats() -> pd.DataFrame:
    """Growth/churn metrics per snapshot (Network Stats tab, §9)."""
    return safe_query(
        f"""
        select
            snapshot_ts,
            snapshot_date_key,
            snapshot_sequence,
            total_connections,
            new_connections,
            lost_connections,
            net_change,
            distinct_companies,
            connections_with_email,
            email_coverage_pct,
            restricted_profile_rows,
            connections_without_company,
            connections_without_position
        from {MART_NETWORK_STATS}
        order by snapshot_ts
        """
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_breakdown(dimension_type: str, top_n: int = 15) -> pd.DataFrame:
    """Top values of one distribution at the latest snapshot."""
    return safe_query(
        f"""
        select
            dimension_value,
            connection_count,
            share_pct
        from {MART_NETWORK_BREAKDOWN}
        where dimension_type = ?
          and snapshot_ts = (select max(snapshot_ts) from {MART_NETWORK_BREAKDOWN})
        order by rank_within_dimension
        limit ?
        """,
        [dimension_type, top_n],
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_connected_over_time() -> pd.DataFrame:
    """Connections made per month, from the latest snapshot."""
    return safe_query(
        f"""
        select
            dimension_value as year_month,
            connection_count
        from {MART_NETWORK_BREAKDOWN}
        where dimension_type = 'connected_year_month'
          and snapshot_ts = (select max(snapshot_ts) from {MART_NETWORK_BREAKDOWN})
        order by dimension_value
        """
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_current_connections() -> pd.DataFrame:
    """Every current connection, for the Job Search ranking (§9).

    `is_current` is derived the way dbt snapshots express it: an open row has
    no `dbt_valid_to`. Recency of change is deliberately absent — it is shown
    in its own panel, not folded into the referral ranking.
    """
    return safe_query(
        f"""
        select
            dim.connection_id,
            dim.full_name,
            dim.first_name,
            dim.last_name,
            dim.email_address,
            dim.company,
            company_dim.company_name,
            company_dim.current_connection_count as company_connection_count,
            dim.position,
            dim.connected_on,
            dim.dbt_valid_to is null as is_current
        from {DIM_CONNECTION} as dim
        left join {DIM_COMPANY} as company_dim
            on dim.company_key = company_dim.company_key
        where dim.dbt_valid_to is null
        order by dim.full_name
        """
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_recent_changes(limit: int = 25) -> pd.DataFrame:
    """Connections whose company/title changed most recently, newest first."""
    return safe_query(
        f"""
        with versions as (

            select
                connection_id,
                count(*) as version_count
            from {DIM_CONNECTION}
            group by connection_id

        )

        select
            dim.full_name,
            dim.company,
            dim.position,
            dim.dbt_valid_from as changed_at,
            dim.connection_id
        from {DIM_CONNECTION} as dim
        inner join versions on dim.connection_id = versions.connection_id
        where dim.dbt_valid_to is null
          and versions.version_count > 1
        order by dim.dbt_valid_from desc
        limit ?
        """,
        [limit],
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_departed_connections(limit: int = 25) -> pd.DataFrame:
    """Connections absent from the latest export.

    Only the fact and the date are shown — no reason is inferred or stored,
    because LinkedIn's export gives no such signal (§5, §14).
    """
    return safe_query(
        f"""
        select
            full_name,
            company,
            position,
            dbt_valid_to as absent_since,
            connection_id
        from {DIM_CONNECTION}
        where dbt_valid_to is not null
          and connection_id not in (
              select connection_id
              from {DIM_CONNECTION}
              where dbt_valid_to is null
          )
        order by dbt_valid_to desc
        limit ?
        """,
        [limit],
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_connection_history(connection_id: str) -> pd.DataFrame:
    """Full SCD2 history of one connection."""
    return safe_query(
        f"""
        select
            company,
            position,
            dbt_valid_from,
            dbt_valid_to,
            dbt_valid_to is null as is_current
        from {DIM_CONNECTION}
        where connection_id = ?
        order by dbt_valid_from
        """,
        [connection_id],
    )


def clear_caches() -> None:
    """Drop cached query results after an ingestion run."""
    st.cache_data.clear()
