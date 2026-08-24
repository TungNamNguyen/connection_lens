"""Airflow REST API wrapper (§8, §9).

Targets Airflow 3's stable API: `/api/v2` with JWT bearer tokens. Airflow 2's
`/api/v1` plus HTTP basic auth is gone — a token is fetched once from
`/auth/token` and refreshed automatically when the API rejects it.

Shared by the Streamlit Job Management tab (trigger mode 3) and by the MinIO
event listener (trigger mode 2) — both call exactly the same endpoint with a
different ``triggered_by`` tag, because the DAG itself must never branch on
which mode woke it up (§5, §17).

Every request/response shaping function is pure so it can be unit-tested with
mocked payloads (§12).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from common.errors import ConnectionLensError
from common.models import DagRunSummary, TaskInstanceSummary, TriggerSource
from common.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v2"

#: Where the FAB auth manager issues JWTs (Airflow 3).
TOKEN_PATH = "/auth/token"

#: The FAB auth manager builds its Flask app lazily, so the very first token
#: request after the API server boots can return a 500. Retry briefly rather
#: than telling the owner Airflow is down.
TOKEN_RETRY_ATTEMPTS = 3
TOKEN_RETRY_DELAY_SECONDS = 1.5

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

    ``logical_date`` is sent explicitly as null: Airflow 3 expects the field to
    be present, and a triggered ingestion has no logical date — it processes
    whatever is in the landing zone right now, not a time slice.
    """
    source = triggered_by.value if isinstance(triggered_by, TriggerSource) else str(triggered_by)
    conf: dict[str, Any] = {"triggered_by": source}
    if conf_extra:
        conf.update(conf_extra)
    payload: dict[str, Any] = {"conf": conf, "logical_date": None}
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
        self._username = username
        self._password = password
        self._token: str | None = None
        self._session = session or requests.Session()
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

    # --- authentication ----------------------------------------------------
    def _fetch_token(self) -> str:
        """Exchange the configured credentials for a JWT (Airflow 3).

        Retries a server-side error a couple of times: a freshly started API
        server can fail its first token request while FAB initialises. Bad
        credentials (4xx) fail immediately — retrying those helps nobody.
        """
        url = f"{self.base_url.rstrip('/')}{TOKEN_PATH}"
        response = None
        for attempt in range(1, TOKEN_RETRY_ATTEMPTS + 1):
            try:
                response = self._session.request(
                    "POST",
                    url,
                    json={"username": self._username, "password": self._password},
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                raise AirflowApiError(f"Could not reach Airflow at {url}: {error}") from error
            if response.status_code < 500:
                break
            logger.warning(
                "Airflow token endpoint returned HTTP %s (attempt %d/%d) — "
                "the API server may still be starting.",
                response.status_code,
                attempt,
                TOKEN_RETRY_ATTEMPTS,
            )
            if attempt < TOKEN_RETRY_ATTEMPTS:
                time.sleep(TOKEN_RETRY_DELAY_SECONDS)

        assert response is not None
        if response.status_code >= 400:
            raise AirflowApiError(
                "Could not obtain an Airflow API token (HTTP "
                f"{response.status_code}). Check AIRFLOW_API_USERNAME and "
                f"AIRFLOW_API_PASSWORD: {response.text[:200]}"
            )
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise AirflowApiError(f"Airflow returned no access_token: {payload!r}")
        return str(token)

    def _auth_header(self) -> dict[str, str]:
        if self._token is None:
            self._token = self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, *, _retry: bool = True, **kwargs: Any) -> Any:
        caller_headers = kwargs.pop("headers", {})
        try:
            response = self._session.request(
                method,
                url,
                timeout=self.timeout,
                headers={**caller_headers, **self._auth_header()},
                **kwargs,
            )
        except requests.RequestException as error:
            raise AirflowApiError(f"Could not reach Airflow at {url}: {error}") from error
        if response.status_code in (401, 403) and _retry:
            # The token expired or was rejected — get a fresh one and retry once.
            self._token = None
            return self._request(
                method, url, _retry=False, headers=caller_headers, **kwargs
            )
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
            payload = self._request("GET", self._url("monitor", "health"))
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
