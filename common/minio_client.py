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
from datetime import datetime

from minio import Minio
from minio.error import S3Error

from common.errors import ObjectKeyError
from common.models import LandingObject
from common.naming import build_object_key, parse_object_key, utcnow
from common.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_CSV_CONTENT_TYPE = "text/csv"


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
        if not self._client.bucket_exists(self.bucket):
            logger.info("Creating MinIO bucket %r", self.bucket)
            self._client.make_bucket(self.bucket)

    def is_reachable(self) -> bool:
        """Return whether MinIO answers — used for Streamlit's status badges."""
        try:
            self._client.bucket_exists(self.bucket)
        except (S3Error, OSError) as error:  # pragma: no cover - network dependent
            logger.warning("MinIO unreachable: %s", error)
            return False
        return True

    # --- write -------------------------------------------------------------
    def put_export(
        self, raw: bytes, file_hash: str, snapshot_ts: datetime | None = None
    ) -> LandingObject:
        """Upload raw export bytes under the documented key convention."""
        snapshot_ts = snapshot_ts or utcnow()
        key = build_object_key(self.raw_prefix, snapshot_ts, file_hash)
        self.ensure_bucket()
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
        for item in self._client.list_objects(
            self.bucket, prefix=f"{self.raw_prefix}/", recursive=True
        ):
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

    def get_object_bytes(self, key: str) -> bytes:
        """Download an object's full content."""
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
