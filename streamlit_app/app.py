"""Connection Lens — Streamlit entrypoint.

Run with::

    streamlit run streamlit_app/app.py

The four tabs live in `streamlit_app/pages/` and Streamlit builds the sidebar
navigation from them (§9). This page is the landing dashboard: is the pipeline
healthy, how big is the network, and where do I go next.
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
from streamlit_app.theme import page_header, section, status_pill  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    APP_TITLE,
    configure_page,
    format_timestamp,
    minio_status,
    render_sidebar_footer,
)

#: Metric cards in a row share a fixed height, so a card carrying a delta does
#: not stand taller than its neighbours.
METRIC_HEIGHT = 128

configure_page("Overview")
require_login()

page_header(
    APP_TITLE,
    "A LinkedIn connections export, turned into network analytics and "
    "warm-intro signal — locally, with a real ingestion pipeline behind it.",
)

settings = get_settings()
status = db.warehouse_status()

# --- Service health --------------------------------------------------------
warehouse_card, landing_card, orchestration_card = st.columns(3, gap="medium")

with warehouse_card, st.container(border=True):
    st.markdown("**Warehouse**")
    if status["warehouse"]:
        layers = [
            ("Bronze", status["bronze"]),
            ("Gold", status["gold"]),
            ("Marts", status["marts"]),
        ]
        st.markdown(
            " ".join(
                status_pill(name, "ok" if ready else "idle") for name, ready in layers
            ),
            unsafe_allow_html=True,
        )
        st.caption(f"`{settings.duckdb_file.name}`")
    else:
        st.markdown(status_pill("Not created yet", "idle"), unsafe_allow_html=True)
        st.caption("The Airflow DAG creates it on its first successful run.")

with landing_card, st.container(border=True):
    st.markdown("**Landing zone**")
    landing_zone = minio_status()
    st.markdown(
        status_pill(
            "Reachable" if landing_zone.is_ready else "Unavailable",
            "ok" if landing_zone.is_ready else "warn",
        ),
        unsafe_allow_html=True,
    )
    st.caption(landing_zone.detail or "—")

with orchestration_card, st.container(border=True):
    st.markdown("**Orchestration**")
    st.markdown(status_pill(settings.airflow_dag_id, "idle"), unsafe_allow_html=True)
    st.caption(f"{settings.airflow_public_url} — status on Job Management")

# --- Headline numbers ------------------------------------------------------
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
    has_history = len(stats) > 1

    st.markdown("")
    metric_columns = st.columns(4, gap="medium")
    metric_columns[0].metric(
        "Current connections",
        f"{int(latest['total_connections']):,}",
        delta=int(latest["net_change"]) if has_history else None,
        delta_description="since the previous snapshot" if has_history else None,
        icon=":material/group:",
        border=True,
        height=METRIC_HEIGHT,
    )
    metric_columns[1].metric(
        "Companies",
        f"{int(latest['distinct_companies']):,}",
        icon=":material/apartment:",
        border=True,
        height=METRIC_HEIGHT,
        help="Distinct employers named in the latest export.",
    )
    metric_columns[2].metric(
        "New in last snapshot",
        f"{int(latest['new_connections']):,}",
        icon=":material/person_add:",
        border=True,
        height=METRIC_HEIGHT,
    )
    metric_columns[3].metric(
        "Left the network",
        f"{int(latest['lost_connections']):,}",
        icon=":material/person_remove:",
        border=True,
        height=METRIC_HEIGHT,
        help="Absent from the latest export. No reason is inferred — "
        "LinkedIn's export gives none.",
    )

    st.caption(
        f"Latest snapshot: {format_timestamp(latest['snapshot_ts'])} · "
        f"{len(stats)} snapshot(s) ingested · "
        f"{int(latest['restricted_profile_rows'])} restricted profile row(s) "
        "excluded from the model"
    )

st.divider()

# --- Where to go next ------------------------------------------------------
section("Where to go next")

nav_columns = st.columns(4, gap="medium")
destinations = [
    ("pages/1_Upload.py", "Upload an export", ":material/upload:",
     "Validate, hash and land a new `Connections.csv`."),
    ("pages/2_Network_Stats.py", "Network stats", ":material/monitoring:",
     "Growth, churn, composition and referral reach."),
    ("pages/3_Job_Search.py", "Job search", ":material/target:",
     "Rank who could realistically refer you."),
    ("pages/4_Job_Management.py", "Job management", ":material/settings:",
     "Trigger the DAG and read its logs."),
]
for column, (path, label, icon, blurb) in zip(nav_columns, destinations, strict=True):
    with column, st.container(border=True):
        st.page_link(path, label=f"**{label}**", icon=icon)
        st.caption(blurb)

st.divider()

with st.expander("How the pipeline works", icon=":material/account_tree:"):
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
