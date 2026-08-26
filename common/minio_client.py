"""MinIO landing-zone client.

Used by two runtimes, which is why it lives in `common/` rather than under
`streamlit_app/`:

* the Streamlit Upload tab **puts** an export here (§7),
* the Airflow DAG **scans and gets** objects from here (§8).

MinIO deliberately keeps every uploaded object, duplicates included — it is the
upload audit trail (§5).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from common.errors import LandingZoneError, ObjectKeyError
from common.models import LandingObject
from common.naming import build_object_key, parse_object_key, utcnow
from common.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_CSV_CONTENT_TYPE = "text/csv"

#: What the MinIO SDK raises when the server cannot be reached at all. Note
#: that `urllib3.exceptions.MaxRetryError` is neither an `S3Error` nor an
#: `OSError`, so it has to be named explicitly.
_TRANSPORT_ERRORS = (HTTPError, OSError)


@dataclass(frozen=True)
class LandingZoneStatus:
    """Whether the landing zone is usable, and why not when it is not."""

    reachable: bool
    bucket_exists: bool
    detail: str

    @property
    def is_ready(self) -> bool:
        return self.reachable and self.bucket_exists


@contextmanager
def _landing_zone_errors(action: str) -> Iterator[None]:
    """Translate MinIO SDK failures into :class:`LandingZoneError`."""
    try:
        yield
    except S3Error as error:
        raise LandingZoneError(
            f"MinIO could not {action}: {error.code} — {error.message}"
        ) from error
    except _TRANSPORT_ERRORS as error:
        raise LandingZoneError(
            f"Could not reach MinIO while trying to {action}: {error}"
        ) from error


class LandingZoneClient:
    """Thin, typed wrapper over the MinIO SDK."""

    def __init__(self, client: Minio, bucket: str, raw_prefix: str) -> None:
        self._client = client
        self.bucket = bucket
        self.raw_prefix = raw_prefix.strip("/")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LandingZoneClient:
        settings = settings or get_settings()
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )
        return cls(client, settings.minio_bucket, settings.minio_raw_prefix)

    # --- bucket ------------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Create the bucket when it does not exist yet."""
        with _landing_zone_errors(f"create bucket {self.bucket!r}"):
            if not self._client.bucket_exists(self.bucket):
                logger.info("Creating MinIO bucket %r", self.bucket)
                self._client.make_bucket(self.bucket)

    def bucket_exists(self) -> bool:
        """Return whether the configured bucket exists."""
        with _landing_zone_errors(f"check bucket {self.bucket!r}"):
            return bool(self._client.bucket_exists(self.bucket))

    def check_status(self) -> LandingZoneStatus:
        """Describe the landing zone for the app's status badges.

        Reachability and bucket existence are separate answers: a running
        MinIO with no bucket is a different problem from a MinIO that is not
        running, and the two need different advice.
        """
        try:
            exists = self.bucket_exists()
        except LandingZoneError as error:
            logger.warning("Landing zone unavailable: %s", error)
            return LandingZoneStatus(reachable=False, bucket_exists=False, detail=str(error))
        if not exists:
            return LandingZoneStatus(
                reachable=True,
                bucket_exists=False,
                detail=(
                    f"Bucket `{self.bucket}` does not exist yet — it is created "
                    "on the first upload, or by `make up`."
                ),
            )
        return LandingZoneStatus(reachable=True, bucket_exists=True, detail="")

    def is_reachable(self) -> bool:
        """Return whether MinIO answers at all."""
        return self.check_status().reachable

    # --- write -------------------------------------------------------------
    def put_export(
        self, raw: bytes, file_hash: str, snapshot_ts: datetime | None = None
    ) -> LandingObject:
        """Upload raw export bytes under the documented key convention."""
        snapshot_ts = snapshot_ts or utcnow()
        key = build_object_key(self.raw_prefix, snapshot_ts, file_hash)
        self.ensure_bucket()
        with _landing_zone_errors(f"upload {key!r}"):
            self._client.put_object(
                self.bucket,
                key,
                io.BytesIO(raw),
                length=len(raw),
                content_type=_CSV_CONTENT_TYPE,
            )
        logger.info("Uploaded %d bytes to s3://%s/%s", len(raw), self.bucket, key)
        return LandingObject(
            key=key,
            snapshot_ts=snapshot_ts,
            hash8=parse_object_key(key).hash8,
            size_bytes=len(raw),
        )

    # --- read --------------------------------------------------------------
    def list_landing_objects(self) -> list[LandingObject]:
        """List every export in the landing zone, oldest snapshot first.

        Objects whose key does not follow the convention are skipped with a
        loud warning rather than being guessed at (§17).
        """
        objects: list[LandingObject] = []
        with _landing_zone_errors(f"list objects in {self.bucket!r}"):
            items = list(
                self._client.list_objects(
                    self.bucket, prefix=f"{self.raw_prefix}/", recursive=True
                )
            )
        for item in items:
            try:
                parts = parse_object_key(item.object_name)
            except ObjectKeyError as error:
                logger.warning("Ignoring object with unrecognised key: %s", error)
                continue
            objects.append(
                LandingObject(
                    key=item.object_name,
                    snapshot_ts=parts.snapshot_ts,
                    hash8=parts.hash8,
                    size_bytes=item.size or 0,
                    last_modified=item.last_modified,
                )
            )
        return sorted(objects, key=lambda obj: (obj.snapshot_ts, obj.key))

    # --- delete ------------------------------------------------------------
    def delete_object(self, key: str) -> int:
        """Permanently remove one landing-zone object and return how many
        versions went with it.

        Two guards, because this is the only destructive operation the app
        exposes:

        * the key must sit under the configured raw prefix, so the UI can
          never reach the rest of the bucket;
        * every version is removed rather than a delete marker being stacked
          on top — the bucket is versioned, so "deleted" has to mean deleted.

        Bronze is untouched. An export already ingested stays in the
        warehouse; only the landing-zone copy goes.
        """
        prefix = f"{self.raw_prefix}/"
        if not key.startswith(prefix):
            raise LandingZoneError(
                f"Refusing to delete {key!r}: only objects under {prefix!r} "
                "can be removed from here."
            )

        with _landing_zone_errors(f"delete {key!r}"):
            versions = [
                DeleteObject(item.object_name, item.version_id)
                for item in self._client.list_objects(
                    self.bucket, prefix=key, include_version=True
                )
                if item.object_name == key
            ]
            if not versions:
                raise LandingZoneError(
                    f"No object named {key!r} in the landing zone."
                )
            failures = [
                f"{error.name}: {error.message}"
                for error in self._client.remove_objects(self.bucket, versions)
            ]

        if failures:
            raise LandingZoneError(
                "MinIO refused to delete: " + "; ".join(failures[:3])
            )
        logger.warning(
            "DELETED %d version(s) of %s from the landing zone.", len(versions), key
        )
        return len(versions)

    def get_object_bytes(self, key: str) -> bytes:
        """Download an object's full content."""
        with _landing_zone_errors(f"download {key!r}"):
            response = self._client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
