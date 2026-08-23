"""Airflow REST API wrapper (§8, §9).

Shared by the Streamlit Job Management tab (trigger mode 3) and by the MinIO
event listener (trigger mode 2) — both call exactly the same endpoint with a
different ``triggered_by`` tag, because the DAG itself must never branch on
which mode woke it up (§5, §17).

Every request/response shaping function is pure so it can be unit-tested with
mocked payloads (§12).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from common.errors import ConnectionLensError
from common.models import DagRunSummary, TaskInstanceSummary, TriggerSource
from common.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v1"

#: Shown when a run carries no ``triggered_by`` — i.e. someone used the
#: Airflow UI's own "Trigger DAG" button (trigger mode 1, §8).
MANUAL_UI_LABEL = TriggerSource.MANUAL_UI.label


class AirflowApiError(ConnectionLensError):
    """The Airflow REST API returned an error or could not be reached."""


# --------------------------------------------------------------------------
# Pure request/response helpers
# --------------------------------------------------------------------------
def build_api_url(base_url: str, *parts: str, api_version: str = DEFAULT_API_VERSION) -> str:
    """Build an absolute Airflow REST API URL from path segments."""
    suffix = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    return f"{base_url.rstrip('/')}/api/{api_version}/{suffix}".rstrip("/")


def build_trigger_payload(
    triggered_by: TriggerSource | str,
    *,
    conf_extra: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build the POST body that starts a DAG run.

    The ``triggered_by`` tag is what lets the Job Management tab attribute a
    run to a source; Airflow's own metadata cannot express it (§5).
    """
    source = triggered_by.value if isinstance(triggered_by, TriggerSource) else str(triggered_by)
    conf: dict[str, Any] = {"triggered_by": source}
    if conf_extra:
        conf.update(conf_extra)
    payload: dict[str, Any] = {"conf": conf}
    if note:
        payload["note"] = note
    return payload


def extract_triggered_by(conf: dict[str, Any] | None) -> str:
    """Read the trigger source from a run's conf, falling back to the UI label."""
    if not conf:
        return MANUAL_UI_LABEL
    raw = str(conf.get("triggered_by", "")).strip()
    if not raw:
        return MANUAL_UI_LABEL
    try:
        return TriggerSource(raw).label
    except ValueError:
        return raw


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an Airflow ISO-8601 timestamp, tolerating the ``Z`` suffix."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparsable timestamp from Airflow API: %r", value)
        return None


def parse_dag_run(payload: dict[str, Any]) -> DagRunSummary:
    """Turn one API dag-run object into a typed summary row (§9)."""
    return DagRunSummary(
        dag_run_id=payload.get("dag_run_id", ""),
        state=payload.get("state", "unknown"),
        triggered_by=extract_triggered_by(payload.get("conf")),
        logical_date=parse_timestamp(payload.get("logical_date") or payload.get("execution_date")),
        start_date=parse_timestamp(payload.get("start_date")),
        end_date=parse_timestamp(payload.get("end_date")),
        note=payload.get("note"),
    )


def parse_dag_runs(payload: dict[str, Any]) -> list[DagRunSummary]:
    """Turn a dag-run collection response into typed summary rows."""
    return [parse_dag_run(item) for item in payload.get("dag_runs", [])]


def parse_task_instances(payload: dict[str, Any]) -> list[TaskInstanceSummary]:
    """Turn a task-instance collection response into typed rows."""
    return [
        TaskInstanceSummary(
            task_id=item.get("task_id", ""),
            state=item.get("state"),
            try_number=int(item.get("try_number") or 1),
        )
        for item in payload.get("task_instances", [])
    ]


def parse_log_response(payload: Any) -> str:
    """Normalise a task-log response (JSON envelope or plain text) to text."""
    if isinstance(payload, dict):
        content = payload.get("content", "")
        return content if isinstance(content, str) else str(content)
    return str(payload)


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------
class AirflowClient:
    """Minimal typed client over the Airflow stable REST API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        dag_id: str,
        timeout: float = 15.0,
        api_version: str = DEFAULT_API_VERSION,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url
        self.dag_id = dag_id
        self.timeout = timeout
        self.api_version = api_version
        self._session = session or requests.Session()
        self._session.auth = (username, password)
        self._session.headers.update({"Accept": "application/json"})

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AirflowClient:
        settings = settings or get_settings()
        return cls(
            settings.airflow_api_base_url,
            settings.airflow_api_username,
            settings.airflow_api_password.get_secret_value(),
            dag_id=settings.airflow_dag_id,
            timeout=settings.airflow_api_timeout_seconds,
        )

    def _url(self, *parts: str) -> str:
        return build_api_url(self.base_url, *parts, api_version=self.api_version)

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self._session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as error:
            raise AirflowApiError(f"Could not reach Airflow at {url}: {error}") from error
        if response.status_code >= 400:
            raise AirflowApiError(
                f"Airflow API {method} {url} failed with HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return response.text

    # --- operations --------------------------------------------------------
    def is_healthy(self) -> bool:
        """Return whether the Airflow API answers and reports a healthy scheduler."""
        try:
            payload = self._request("GET", self._url("health"))
        except AirflowApiError as error:
            logger.warning("Airflow health check failed: %s", error)
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("metadatabase", {}).get("status") == "healthy"

    def trigger_dag_run(
        self,
        triggered_by: TriggerSource | str,
        *,
        conf_extra: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> DagRunSummary:
        """Start a DAG run tagged with its trigger source (trigger modes 2 & 3)."""
        payload = build_trigger_payload(triggered_by, conf_extra=conf_extra, note=note)
        logger.info("Triggering DAG %s with conf=%s", self.dag_id, payload["conf"])
        response = self._request(
            "POST", self._url("dags", self.dag_id, "dagRuns"), json=payload
        )
        return parse_dag_run(response)

    def list_dag_runs(self, limit: int = 20) -> list[DagRunSummary]:
        """Return the most recent runs, newest first (§9 run-history table)."""
        response = self._request(
            "GET",
            self._url("dags", self.dag_id, "dagRuns"),
            params={"limit": limit, "order_by": "-start_date"},
        )
        return parse_dag_runs(response if isinstance(response, dict) else {})

    def list_task_instances(self, dag_run_id: str) -> list[TaskInstanceSummary]:
        """Return the task instances of one run."""
        response = self._request(
            "GET", self._url("dags", self.dag_id, "dagRuns", dag_run_id, "taskInstances")
        )
        return parse_task_instances(response if isinstance(response, dict) else {})

    def get_task_log(self, dag_run_id: str, task_id: str, try_number: int = 1) -> str:
        """Fetch one task instance's log so the owner never has to open Airflow (§9)."""
        response = self._request(
            "GET",
            self._url(
                "dags",
                self.dag_id,
                "dagRuns",
                dag_run_id,
                "taskInstances",
                task_id,
                "logs",
                str(try_number),
            ),
            params={"full_content": "true"},
        )
        return parse_log_response(response)

    def get_dag(self) -> dict[str, Any]:
        """Return the DAG's metadata (used to surface a paused DAG in the UI)."""
        response = self._request("GET", self._url("dags", self.dag_id))
        return response if isinstance(response, dict) else {}

    def set_paused(self, is_paused: bool) -> dict[str, Any]:
        """Pause/unpause the DAG from the Job Management tab."""
        response = self._request(
            "PATCH",
            self._url("dags", self.dag_id),
            params={"update_mask": "is_paused"},
            json={"is_paused": is_paused},
        )
        return response if isinstance(response, dict) else {}
