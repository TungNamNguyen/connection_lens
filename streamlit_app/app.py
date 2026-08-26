"""Connection Lens — Streamlit entrypoint.

Run with::

    streamlit run streamlit_app/app.py

The four tabs live in `streamlit_app/pages/` and Streamlit builds the sidebar
navigation from them (§9).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from common.settings import get_settings  # noqa: E402
from streamlit_app import db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    APP_TITLE,
    configure_page,
    format_timestamp,
    minio_status,
    render_sidebar_footer,
)

configure_page("Overview")
require_login()

st.title(f"🔗 {APP_TITLE}")
st.caption(
    "Turn a LinkedIn connections export into network analytics and warm-intro "
    "signal — locally, with a real ingestion pipeline behind it."
)

settings = get_settings()
status = db.warehouse_status()

col_warehouse, col_minio, col_airflow = st.columns(3)

with col_warehouse:
    st.markdown("**Warehouse**")
    if status["warehouse"]:
        layers = [
            ("Bronze", status["bronze"]),
            ("Gold", status["gold"]),
            ("Marts", status["marts"]),
        ]
        st.write(
            " ".join(f"{'✅' if ready else '⬜'} {name}" for name, ready in layers)
        )
        st.caption(f"`{settings.duckdb_file}`")
    else:
        st.write("⬜ Not created yet")
        st.caption("The Airflow DAG creates it on its first successful run.")

with col_minio:
    st.markdown("**Landing zone**")
    landing_zone = minio_status()
    st.write("✅ Reachable" if landing_zone.is_ready else "⚠️ Unavailable")
    st.caption(landing_zone.detail or "—")

with col_airflow:
    st.markdown("**Orchestration**")
    st.write(f"DAG `{settings.airflow_dag_id}`")
    st.caption(f"{settings.airflow_public_url} — status on the Job Management tab")

st.divider()

stats = db.load_network_stats()
if stats.empty:
    st.info(
        "**No snapshots ingested yet.** Start on the **Upload** tab: drop your "
        "LinkedIn `Connections.csv` there, then trigger the ingestion DAG from "
        "the **Job Management** tab.",
        icon="👋",
    )
else:
    latest = stats.iloc[-1]
    previous = stats.iloc[-2] if len(stats) > 1 else None

    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "Current connections",
        f"{int(latest['total_connections']):,}",
        delta=None if previous is None else int(latest["net_change"]),
    )
    metric_columns[1].metric("Companies", f"{int(latest['distinct_companies']):,}")
    metric_columns[2].metric(
        "New in last snapshot", f"{int(latest['new_connections']):,}"
    )
    metric_columns[3].metric(
        "Left the network", f"{int(latest['lost_connections']):,}"
    )

    st.caption(
        f"Latest snapshot: {format_timestamp(latest['snapshot_ts'])} · "
        f"{len(stats)} snapshot(s) ingested · "
        f"{int(latest['restricted_profile_rows'])} restricted profile row(s) "
        "excluded from the model"
    )

st.divider()

with st.expander("How the pipeline works"):
    st.markdown(
        """
| Stage | What happens |
| --- | --- |
| **Upload** | The export is validated, MD5-hashed and landed in MinIO at `raw/linkedin_connections/<snapshot_ts>_<hash8>.csv`. Uploading never triggers ingestion. |
| **Trigger** | The DAG can start three ways — Airflow UI, a MinIO bucket event, or the Job Management button. Its logic is identical for all three. |
| **Bronze** | The DAG scans MinIO for content hashes not yet in Bronze and appends only genuinely new exports. Duplicates are skipped loudly. |
| **Quality** | A Great Expectations checkpoint validates the landed batch before dbt runs. |
| **Silver → Gold** | dbt cleans and types the export, then keeps SCD Type 2 history in `dim_connection` and one fact row per connection per snapshot. |
| **Serving** | These tabs read the warehouse read-only — every write happens inside Airflow. |

Idempotency is decided by **file content hash against Bronze** — never by
date, upload time or which trigger fired the run.
        """
    )

render_sidebar_footer()
