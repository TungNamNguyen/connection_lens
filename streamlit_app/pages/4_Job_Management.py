"""Job Management tab — trigger the DAG and watch it, without opening Airflow (§9).

This is trigger mode 3: the button calls the same Airflow REST endpoint the
MinIO event listener uses, only tagged `triggered_by="streamlit"` so the run
history can attribute it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common.airflow_client import AirflowApiError, AirflowClient  # noqa: E402
from common.errors import ConnectionLensError  # noqa: E402
from common.models import TriggerSource  # noqa: E402
from common.settings import get_settings  # noqa: E402
from streamlit_app import db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.theme import page_header, section, status_pill  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    format_duration,
    format_timestamp,
    landing_zone_client,
    render_sidebar_footer,
)

configure_page("Job Management")
require_login()

page_header(
    "Job management",
    "Trigger the ingestion DAG and watch it, without opening Airflow. The DAG "
    "behaves identically whichever of the three modes started it.",
)

settings = get_settings()

if not settings.has_airflow_credentials:
    st.error(
        "No Airflow API credentials configured. Copy `.env.example` to `.env` "
        "and set `AIRFLOW_API_USERNAME` / `AIRFLOW_API_PASSWORD`.",
        icon="🚫",
    )
    st.stop()

client = AirflowClient.from_settings(settings)
healthy = client.is_healthy()

airflow_card, dag_card, link_card = st.columns(3, gap="medium")

with airflow_card, st.container(border=True):
    st.markdown("**Airflow**")
    st.markdown(
        status_pill("Healthy" if healthy else "Unreachable", "ok" if healthy else "warn"),
        unsafe_allow_html=True,
    )
    st.caption(settings.airflow_public_url)

with dag_card, st.container(border=True):
    st.markdown("**DAG**")
    st.markdown(status_pill(settings.airflow_dag_id, "idle"), unsafe_allow_html=True)
    st.caption("Runs are serialised — one at a time.")

with link_card, st.container(border=True):
    st.markdown("**Airflow UI**")
    st.markdown(
        f"[Open the DAG ↗]({settings.airflow_public_url}/dags/{settings.airflow_dag_id})"
    )
    st.caption("For anything this tab does not cover.")

if not healthy:
    st.warning(
        "Airflow is not answering. Start it with "
        "`docker compose up -d`, then reload.",
        icon="⚠️",
    )
    st.stop()

try:
    dag_metadata = client.get_dag()
except AirflowApiError as error:
    st.error(f"Could not read the DAG: {error}", icon="🚫")
    st.stop()

if dag_metadata.get("is_paused"):
    st.warning(
        "The DAG is paused — triggered runs will queue but never start.",
        icon="⏸️",
    )
    if st.button("Unpause the DAG"):
        client.set_paused(False)
        st.rerun()

# --- Pending work ----------------------------------------------------------
st.divider()
section(
    "Pending work",
    "Objects in the landing zone whose content hash is not yet in Bronze.",
)

pending_count = 0
# This is the one page you are most likely to be on *during* a run, so it never
# halts on a locked warehouse — it just says which number it cannot compute.
warehouse_busy = db.warehouse_status()["busy"]
try:
    objects = landing_zone_client().list_landing_objects()
    ingested_short = {value[:8] for value in db.bronze_file_hashes()}
    pending = [obj for obj in objects if obj.hash8 not in ingested_short]
    pending_count = len(pending)
except ConnectionLensError as error:
    st.caption(f"Could not read the landing zone: {error}")
    pending = []

if warehouse_busy:
    st.caption(
        "Ingestion is running — Bronze cannot be read, so everything in the "
        "landing zone counts as pending until it finishes."
    )

pending_columns = st.columns([1, 3], gap="medium")
pending_columns[0].metric(
    "Awaiting ingestion",
    pending_count,
    icon=":material/pending_actions:",
    border=True,
)
if pending:
    pending_columns[1].dataframe(
        pd.DataFrame(
            {
                "Object": [obj.key.rsplit("/", 1)[-1] for obj in pending],
                "Snapshot": [format_timestamp(obj.snapshot_ts) for obj in pending],
            }
        ),
        width="stretch",
        hide_index=True,
    )
else:
    pending_columns[1].caption(
        "Nothing new in the landing zone. Triggering anyway is a safe no-op — "
        "the DAG skips content already in Bronze."
    )

# --- Trigger ---------------------------------------------------------------
st.divider()
section("Trigger ingestion")

trigger_columns = st.columns([2, 3], gap="medium")
with trigger_columns[0]:
    force_transform = st.checkbox(
        "Rebuild dbt models even if nothing new landed",
        value=False,
        help=(
            "Off by default: with nothing pending the run is a deliberate "
            "no-op. Turn on to rebuild Silver/Gold from existing Bronze data."
        ),
    )
    if st.button(
        "Trigger ingestion now", type="primary", icon=":material/play_arrow:"
    ):
        try:
            run = client.trigger_dag_run(
                TriggerSource.STREAMLIT,
                conf_extra={"force_transform": force_transform},
                note="Triggered from the Connection Lens Job Management tab.",
            )
        except AirflowApiError as error:
            st.error(f"Trigger failed: {error}", icon="🚫")
        else:
            st.success(f"Started run `{run.dag_run_id}`", icon="🚀")
            db.clear_caches()

with trigger_columns[1]:
    st.markdown(
        """
The run scans the landing zone for content not yet in Bronze, so triggering
with nothing pending is a safe no-op.

The Airflow UI and MinIO bucket events start the same run.
        """
    )

# --- Run history -----------------------------------------------------------
st.divider()
history_header, refresh_column = st.columns([4, 1], gap="medium")
with history_header:
    section("Run history")
if refresh_column.button(
    "Refresh", icon=":material/refresh:", use_container_width=True
):
    st.rerun()

try:
    runs = client.list_dag_runs(limit=20)
except AirflowApiError as error:
    st.error(f"Could not list runs: {error}", icon="🚫")
    runs = []

if not runs:
    st.caption("No runs yet.")
    render_sidebar_footer()
    st.stop()

state_icons = {
    "success": "✅ success",
    "failed": "❌ failed",
    "running": "🔄 running",
    "queued": "⏳ queued",
}

st.dataframe(
    pd.DataFrame(
        {
            "Run": [run.dag_run_id for run in runs],
            "Triggered by": [run.triggered_by for run in runs],
            "State": [state_icons.get(run.state, run.state) for run in runs],
            "Started": [format_timestamp(run.start_date) for run in runs],
            "Duration": [format_duration(run.duration_seconds) for run in runs],
        }
    ),
    width="stretch",
    hide_index=True,
    row_height=34,
    column_config={
        "Run": st.column_config.TextColumn("Run", pinned=True, width="medium"),
        "State": st.column_config.TextColumn("State", width="small"),
        "Duration": st.column_config.TextColumn("Duration", width="small"),
    },
)

# --- Logs ------------------------------------------------------------------
st.divider()
section("Logs", "Read a task's output here instead of opening the Airflow UI.")

selected_run = st.selectbox("Run", [run.dag_run_id for run in runs])
try:
    task_instances = client.list_task_instances(selected_run)
except AirflowApiError as error:
    st.error(f"Could not list tasks: {error}", icon="🚫")
    task_instances = []

if not task_instances:
    st.caption("This run has no task instances yet.")
else:
    task_labels = {
        f"{task.task_id} — {task.state or 'pending'}": task for task in task_instances
    }
    default_index = 0
    for index, (_, task) in enumerate(task_labels.items()):
        if task.state == "failed":
            default_index = index
            break
    selected_label = st.selectbox("Task", list(task_labels), index=default_index)
    selected_task = task_labels[selected_label]

    with st.expander("Task log", expanded=True, icon=":material/terminal:"):
        try:
            log_text = client.get_task_log(
                selected_run, selected_task.task_id, max(selected_task.try_number, 1)
            )
        except AirflowApiError as error:
            st.error(f"Could not fetch the log: {error}", icon="🚫")
        else:
            st.code(log_text or "(empty log)", language="text")

render_sidebar_footer()
