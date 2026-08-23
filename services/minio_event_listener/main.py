"""MinIO bucket event -> Airflow DAG trigger (trigger mode 2, §8).

MinIO's webhook payload is not shaped for Airflow's REST API and cannot carry
Airflow's auth headers, so this small service translates one into the other:

    MinIO  --s3:ObjectCreated:*-->  this service  --REST--> Airflow DAG run

The run is tagged `triggered_by="minio_event"` purely so the Job Management
tab can attribute it. The DAG itself behaves identically for every trigger
mode — the object key below is passed as **metadata only** and the DAG never
reads it; it always rescans the landing zone (§5, §17).

Run it with::

    uvicorn services.minio_event_listener.main:app --port 8000
"""

from __future__ import annotations

import logging
import sys
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException, status  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from common.airflow_client import AirflowApiError, AirflowClient  # noqa: E402
from common.models import TriggerSource  # noqa: E402
from common.settings import Settings, get_settings  # noqa: E402

logger = logging.getLogger(__name__)

OBJECT_CREATED_PREFIX = "s3:ObjectCreated:"

app = FastAPI(
    title="Connection Lens — MinIO event listener",
    description=__doc__,
    version="1.0.0",
)


class EventResponse(BaseModel):
    """What the listener did with a bucket notification."""

    triggered: bool
    dag_run_id: str | None = None
    matched_objects: list[str] = []
    detail: str


def extract_created_objects(payload: dict[str, Any], raw_prefix: str) -> list[str]:
    """Return the object keys created by this notification, under `raw_prefix`.

    MinIO percent-encodes keys inside ``Records[].s3.object.key``; anything
    that is not an ObjectCreated event, or lands outside the landing-zone
    prefix, is ignored.
    """
    keys: list[str] = []
    for record in payload.get("Records") or []:
        event_name = str(record.get("eventName", ""))
        if not event_name.startswith(OBJECT_CREATED_PREFIX):
            continue
        raw_key = record.get("s3", {}).get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(str(raw_key))
        if key.startswith(raw_prefix.strip("/")):
            keys.append(key)
    return keys


def verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Check the shared bearer token when one is configured."""
    expected = settings.minio_event_listener_token.get_secret_value()
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing listener token.",
        )


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Liveness plus whether Airflow is reachable from here."""
    client = AirflowClient.from_settings(settings)
    return {
        "status": "ok",
        "dag_id": settings.airflow_dag_id,
        "airflow_reachable": client.is_healthy(),
    }


@app.post("/minio/events", response_model=EventResponse)
def handle_minio_event(
    payload: dict[str, Any],
    settings: Settings = Depends(get_settings),
    _: None = Depends(verify_token),
) -> EventResponse:
    """Translate an ObjectCreated notification into a tagged DAG run."""
    keys = extract_created_objects(payload, settings.minio_raw_prefix)
    if not keys:
        detail = (
            "No ObjectCreated event under "
            f"'{settings.minio_raw_prefix}' in this notification — ignored."
        )
        logger.info(detail)
        return EventResponse(triggered=False, detail=detail)

    client = AirflowClient.from_settings(settings)
    try:
        run = client.trigger_dag_run(
            TriggerSource.MINIO_EVENT,
            # Metadata only: the DAG never branches on this (§5, §17).
            conf_extra={"source_object": keys[0]},
            note=f"Bucket event for {keys[0]}",
        )
    except AirflowApiError as error:
        logger.error("Could not trigger the DAG: %s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error

    detail = f"Triggered {settings.airflow_dag_id} for {len(keys)} new object(s)."
    logger.info("%s Run id: %s", detail, run.dag_run_id)
    return EventResponse(
        triggered=True,
        dag_run_id=run.dag_run_id,
        matched_objects=keys,
        detail=detail,
    )
