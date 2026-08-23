"""Airflow REST request building and response parsing, fully mocked (§12)."""

from __future__ import annotations

from typing import Any

import pytest

from common.airflow_client import (
    MANUAL_UI_LABEL,
    AirflowApiError,
    AirflowClient,
    build_api_url,
    build_trigger_payload,
    extract_triggered_by,
    parse_dag_run,
    parse_dag_runs,
    parse_log_response,
    parse_task_instances,
    parse_timestamp,
)
from common.models import TriggerSource


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""
        self.content = b"x" if payload is not None or text else b""

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.auth: tuple[str, str] | None = None
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def make_client(response: FakeResponse) -> tuple[AirflowClient, FakeSession]:
    session = FakeSession(response)
    client = AirflowClient(
        "http://airflow:8080",
        "airflow",
        "secret",
        dag_id="ingest_connections",
        session=session,  # type: ignore[arg-type]
    )
    return client, session


# --- pure helpers ----------------------------------------------------------
def test_build_api_url_joins_segments() -> None:
    assert (
        build_api_url("http://airflow:8080/", "dags", "ingest_connections", "dagRuns")
        == "http://airflow:8080/api/v1/dags/ingest_connections/dagRuns"
    )


def test_trigger_payload_tags_the_source() -> None:
    payload = build_trigger_payload(TriggerSource.STREAMLIT)
    assert payload == {"conf": {"triggered_by": "streamlit"}}


def test_trigger_payload_carries_extra_conf_and_a_note() -> None:
    payload = build_trigger_payload(
        TriggerSource.MINIO_EVENT, conf_extra={"source_object": "raw/x.csv"}, note="hi"
    )
    assert payload["conf"]["triggered_by"] == "minio_event"
    assert payload["conf"]["source_object"] == "raw/x.csv"
    assert payload["note"] == "hi"


@pytest.mark.parametrize(
    ("conf", "expected"),
    [
        ({"triggered_by": "streamlit"}, "Streamlit"),
        ({"triggered_by": "minio_event"}, "MinIO event"),
        ({}, MANUAL_UI_LABEL),
        (None, MANUAL_UI_LABEL),
        ({"triggered_by": ""}, MANUAL_UI_LABEL),
        ({"triggered_by": "something_else"}, "something_else"),
    ],
)
def test_triggered_by_falls_back_to_the_ui_label(conf, expected) -> None:
    """Airflow's own metadata cannot say *why* an API run happened (§5, §9)."""
    assert extract_triggered_by(conf) == expected


def test_parse_timestamp_handles_z_suffix_and_junk() -> None:
    assert parse_timestamp("2026-08-23T14:05:01Z") is not None
    assert parse_timestamp(None) is None
    assert parse_timestamp("not-a-date") is None


def test_parse_dag_run_maps_every_column_of_the_history_table() -> None:
    run = parse_dag_run(
        {
            "dag_run_id": "manual__2026-08-23T14:05:01+00:00",
            "state": "success",
            "conf": {"triggered_by": "streamlit"},
            "start_date": "2026-08-23T14:05:01Z",
            "end_date": "2026-08-23T14:06:11Z",
            "note": "from the app",
        }
    )
    assert run.state == "success"
    assert run.triggered_by == "Streamlit"
    assert run.duration_seconds == 70.0


def test_parse_dag_runs_and_task_instances() -> None:
    runs = parse_dag_runs({"dag_runs": [{"dag_run_id": "a", "state": "queued"}]})
    assert [run.dag_run_id for run in runs] == ["a"]

    tasks = parse_task_instances(
        {"task_instances": [{"task_id": "scan_landing_zone", "state": "success", "try_number": 2}]}
    )
    assert tasks[0].task_id == "scan_landing_zone"
    assert tasks[0].try_number == 2


def test_parse_log_response_accepts_json_or_text() -> None:
    assert parse_log_response({"content": "line one"}) == "line one"
    assert parse_log_response("plain text") == "plain text"


# --- client behaviour ------------------------------------------------------
def test_trigger_posts_to_the_documented_endpoint() -> None:
    client, session = make_client(FakeResponse({"dag_run_id": "run-1", "state": "queued"}))
    run = client.trigger_dag_run(TriggerSource.STREAMLIT)

    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/dags/ingest_connections/dagRuns")
    assert call["json"]["conf"]["triggered_by"] == "streamlit"
    assert run.dag_run_id == "run-1"


def test_list_dag_runs_requests_newest_first() -> None:
    client, session = make_client(FakeResponse({"dag_runs": []}))
    client.list_dag_runs(limit=5)
    assert session.calls[0]["params"] == {"limit": 5, "order_by": "-start_date"}


def test_get_task_log_asks_for_the_full_content() -> None:
    client, session = make_client(FakeResponse({"content": "log body"}))
    assert client.get_task_log("run-1", "ingest_new_objects_to_bronze", 2) == "log body"
    assert session.calls[0]["url"].endswith(
        "/dagRuns/run-1/taskInstances/ingest_new_objects_to_bronze/logs/2"
    )
    assert session.calls[0]["params"] == {"full_content": "true"}


def test_http_errors_are_raised_with_context() -> None:
    client, _ = make_client(FakeResponse(None, status_code=403, text="Forbidden"))
    with pytest.raises(AirflowApiError, match="403"):
        client.list_dag_runs()


def test_health_reports_unhealthy_metadatabase() -> None:
    client, _ = make_client(FakeResponse({"metadatabase": {"status": "unhealthy"}}))
    assert client.is_healthy() is False

    client, _ = make_client(FakeResponse({"metadatabase": {"status": "healthy"}}))
    assert client.is_healthy() is True
