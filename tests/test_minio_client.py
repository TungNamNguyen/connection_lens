"""Landing-zone client: nothing from the MinIO SDK may reach the UI (§9).

The Streamlit pages only catch `ConnectionLensError`, so every SDK failure —
including `urllib3.exceptions.MaxRetryError`, which is neither an `S3Error`
nor an `OSError` — has to be translated on the way out.
"""

from __future__ import annotations

from typing import Any

import pytest
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from common.errors import ConnectionLensError, LandingZoneError
from common.minio_client import LandingZoneClient

BUCKET = "connection-lens"
PREFIX = "raw/linkedin_connections"


def s3_error(code: str) -> S3Error:
    return S3Error(
        response=None,
        code=code,
        message=f"simulated {code}",
        resource=f"/{BUCKET}",
        request_id="req-1",
        host_id="host-1",
        bucket_name=BUCKET,
    )


class StubMinio:
    """Minimal stand-in for the MinIO SDK client."""

    def __init__(self, *, exists: bool = True, failure: Exception | None = None) -> None:
        self.exists = exists
        self.failure = failure
        self.made_buckets: list[str] = []

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            raise self.failure

    def bucket_exists(self, bucket: str) -> bool:
        self._maybe_fail()
        return self.exists

    def make_bucket(self, bucket: str) -> None:
        self._maybe_fail()
        self.made_buckets.append(bucket)
        self.exists = True

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False) -> Any:
        self._maybe_fail()
        return iter(())

    def get_object(self, bucket: str, key: str) -> Any:
        self._maybe_fail()
        raise AssertionError("unreachable in these tests")


def make_client(**kwargs: Any) -> tuple[LandingZoneClient, StubMinio]:
    stub = StubMinio(**kwargs)
    return LandingZoneClient(stub, BUCKET, PREFIX), stub  # type: ignore[arg-type]


UNREACHABLE = MaxRetryError(pool=None, url="http://minio:9000", reason=None)  # type: ignore[arg-type]


# --- status ----------------------------------------------------------------
def test_a_ready_landing_zone_reports_ready() -> None:
    client, _ = make_client(exists=True)
    status = client.check_status()
    assert status.is_ready
    assert status.reachable and status.bucket_exists


def test_a_missing_bucket_is_not_an_outage() -> None:
    """A running MinIO with no bucket needs different advice from a dead one."""
    client, _ = make_client(exists=False)
    status = client.check_status()
    assert status.reachable is True
    assert status.bucket_exists is False
    assert not status.is_ready
    assert "does not exist" in status.detail


def test_an_unreachable_server_is_reported_as_unreachable() -> None:
    client, _ = make_client(failure=UNREACHABLE)
    status = client.check_status()
    assert status.reachable is False
    assert client.is_reachable() is False
    assert "Could not reach MinIO" in status.detail


# --- error translation -----------------------------------------------------
def test_a_missing_bucket_raises_a_project_error_when_listing() -> None:
    client, _ = make_client(failure=s3_error("NoSuchBucket"))
    with pytest.raises(LandingZoneError, match="NoSuchBucket"):
        client.list_landing_objects()


def test_transport_failures_are_translated_too() -> None:
    """MaxRetryError is neither S3Error nor OSError — it must still translate."""
    client, _ = make_client(failure=UNREACHABLE)
    with pytest.raises(LandingZoneError, match="Could not reach MinIO"):
        client.list_landing_objects()


@pytest.mark.parametrize("failure", [s3_error("AccessDenied"), UNREACHABLE])
def test_every_operation_translates_sdk_errors(failure: Exception) -> None:
    client, _ = make_client(failure=failure)
    for call in (
        client.ensure_bucket,
        client.bucket_exists,
        client.list_landing_objects,
        lambda: client.put_export(b"x", "0" * 32),
        lambda: client.get_object_bytes("raw/x.csv"),
    ):
        with pytest.raises(ConnectionLensError):
            call()


def test_ensure_bucket_creates_a_missing_bucket() -> None:
    client, stub = make_client(exists=False)
    client.ensure_bucket()
    assert stub.made_buckets == [BUCKET]
