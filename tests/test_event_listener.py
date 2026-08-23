"""MinIO bucket event -> Airflow trigger (trigger mode 2, §8)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from common.models import DagRunSummary, TriggerSource
from common.settings import Settings, get_settings
from services.minio_event_listener import main as listener

RAW_PREFIX = "raw/linkedin_connections"
OBJECT_KEY = f"{RAW_PREFIX}/20260823T140501Z_1f3c9ab2.csv"


def created_event(key: str = OBJECT_KEY, event: str = "s3:ObjectCreated:Put") -> dict[str, Any]:
    encoded = key.replace("/", "%2F")
    return {"Records": [{"eventName": event, "s3": {"object": {"key": encoded}}}]}


class SpyAirflowClient:
    """Records what the listener asked Airflow to do."""

    instances: ClassVar[list[SpyAirflowClient]] = []

    def __init__(self) -> None:
        self.triggers: list[dict[str, Any]] = []
        SpyAirflowClient.instances.append(self)

    def trigger_dag_run(self, triggered_by, *, conf_extra=None, note=None) -> DagRunSummary:
        self.triggers.append(
            {"triggered_by": triggered_by, "conf_extra": conf_extra, "note": note}
        )
        return DagRunSummary(dag_run_id="run-1", state="queued", triggered_by="MinIO event")

    def is_healthy(self) -> bool:
        return True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    SpyAirflowClient.instances.clear()
    monkeypatch.setattr(
        listener.AirflowClient, "from_settings", staticmethod(lambda *_: SpyAirflowClient())
    )
    listener.app.dependency_overrides[get_settings] = lambda: Settings(
        minio_raw_prefix=RAW_PREFIX, minio_event_listener_token=SecretStr("")
    )
    yield TestClient(listener.app)
    listener.app.dependency_overrides.clear()


def test_object_created_triggers_a_tagged_run(client: TestClient) -> None:
    response = client.post("/minio/events", json=created_event())
    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["dag_run_id"] == "run-1"

    trigger = SpyAirflowClient.instances[-1].triggers[0]
    assert trigger["triggered_by"] is TriggerSource.MINIO_EVENT
    # Metadata only — the DAG must not branch on it (§5, §17).
    assert trigger["conf_extra"] == {"source_object": OBJECT_KEY}


def test_non_created_events_are_ignored(client: TestClient) -> None:
    response = client.post(
        "/minio/events", json=created_event(event="s3:ObjectRemoved:Delete")
    )
    assert response.json()["triggered"] is False
    # Not even an Airflow client is constructed for an irrelevant event.
    assert SpyAirflowClient.instances == []


def test_objects_outside_the_landing_prefix_are_ignored(client: TestClient) -> None:
    response = client.post("/minio/events", json=created_event(key="other/thing.csv"))
    assert response.json()["triggered"] is False


def test_an_empty_payload_is_not_an_error(client: TestClient) -> None:
    response = client.post("/minio/events", json={})
    assert response.status_code == 200
    assert response.json()["triggered"] is False


def test_health_endpoint(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["airflow_reachable"] is True


def test_percent_encoded_keys_are_decoded() -> None:
    keys = listener.extract_created_objects(created_event(), RAW_PREFIX)
    assert keys == [OBJECT_KEY]


def test_token_is_enforced_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    SpyAirflowClient.instances.clear()
    monkeypatch.setattr(
        listener.AirflowClient, "from_settings", staticmethod(lambda *_: SpyAirflowClient())
    )
    listener.app.dependency_overrides[get_settings] = lambda: Settings(
        minio_raw_prefix=RAW_PREFIX, minio_event_listener_token=SecretStr("s3cret")
    )
    with TestClient(listener.app) as test_client:
        assert test_client.post("/minio/events", json=created_event()).status_code == 401
        authorised = test_client.post(
            "/minio/events",
            json=created_event(),
            headers={"Authorization": "Bearer s3cret"},
        )
        assert authorised.status_code == 200
    listener.app.dependency_overrides.clear()
