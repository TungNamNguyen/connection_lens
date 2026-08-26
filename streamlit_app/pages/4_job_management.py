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
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    format_duration,
    format_timestamp,
    landing_zone_client,
    render_sidebar_footer,
)

configure_page("Job Management")
require_login()
st.title("⚙️ Job management")

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

status_columns = st.columns([2, 2, 3])
status_columns[0].markdown(
    f"**Airflow** {'✅ healthy' if healthy else '⚠️ unreachable'}"
)
status_columns[0].caption(settings.airflow_public_url)
status_columns[1].markdown(f"**DAG** `{settings.airflow_dag_id}`")
status_columns[2].markdown(
    f"[Open the Airflow UI ↗]({settings.airflow_public_url}/dags/{settings.airflow_dag_id})"
)

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
st.subheader("Pending work")

pending_count = 0
try:
    objects = landing_zone_client().list_landing_objects()
    ingested_short = {value[:8] for value in db.bronze_file_hashes()}
    pending = [obj for obj in objects if obj.hash8 not in ingested_short]
    pending_count = len(pending)
except ConnectionLensError as error:
    st.caption(f"Could not read the landing zone: {error}")
    pending = []

pending_columns = st.columns([1, 3])
pending_columns[0].metric("Objects awaiting ingestion", pending_count)
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
st.subheader("Trigger ingestion")

trigger_columns = st.columns([2, 3])
with trigger_columns[0]:
    force_transform = st.checkbox(
        "Rebuild dbt models even if nothing new landed",
        value=False,
        help=(
            "Off by default: with nothing pending the run is a deliberate "
            "no-op. Turn on to rebuild Silver/Gold from existing Bronze data."
        ),
    )
    if st.button("Trigger ingestion now", type="primary"):
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
The DAG behaves identically no matter what starts it:

1. **Airflow UI** — the native *Trigger DAG* button;
2. **MinIO bucket event** — the listener service turns an `s3:ObjectCreated:*`
   notification into this same API call;
3. **this button**.

It always scans MinIO for content hashes not yet in Bronze, so a redundant or
overlapping trigger is a safe no-op, never a duplicate write.
        """
    )

# --- Run history -----------------------------------------------------------
st.divider()
history_header, refresh_column = st.columns([4, 1])
history_header.subheader("Run history")
if refresh_column.button("Refresh"):
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
)

# --- Logs ------------------------------------------------------------------
st.divider()
st.subheader("Logs")

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

    with st.expander("Task log", expanded=True):
        try:
            log_text = client.get_task_log(
                selected_run, selected_task.task_id, max(selected_task.try_number, 1)
            )
        except AirflowApiError as error:
            st.error(f"Could not fetch the log: {error}", icon="🚫")
        else:
            st.code(log_text or "(empty log)", language="text")

render_sidebar_footer()
