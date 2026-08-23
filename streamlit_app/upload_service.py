"""Upload tab logic: validate -> hash -> check Bronze -> land in MinIO (§7).

Kept out of the page file so the whole flow is unit-testable with a fake
landing-zone client (§11, §12). The order matters:

1. **Layer-1 validation** first — a file missing a required column never gets
   hashed or uploaded (§14 scenario 6);
2. the MD5 of the raw bytes is the idempotency key, computed client-side;
3. the hash is checked against **Bronze**, the dataset of record — not against
   MinIO, which intentionally keeps duplicates as an audit trail (§5);
4. the file is uploaded either way; a duplicate simply gets a clear message
   that no new dataset will be created.

This tab never triggers ingestion — that lives in Job Management (§7, §9).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from common.csv_schema import ParsedExport, parse_export
from common.hashing import md5_bytes
from common.minio_client import LandingZoneClient
from common.models import UploadResult
from common.naming import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedUpload:
    """A validated, hashed file that has not been uploaded yet."""

    raw: bytes
    file_hash: str
    parsed: ParsedExport

    @property
    def row_count(self) -> int:
        return self.parsed.row_count


def prepare_upload(raw: bytes) -> PreparedUpload:
    """Validate and hash an uploaded file.

    Raises :class:`common.errors.CsvSchemaError` when the file is not a usable
    LinkedIn export — deliberately *before* any hashing or upload happens.
    """
    parsed = parse_export(raw)
    file_hash = md5_bytes(raw)
    logger.info(
        "Prepared upload: %d row(s), md5=%s, header detected on line %d (%s).",
        parsed.row_count,
        file_hash,
        parsed.header_line_index + 1,
        parsed.encoding,
    )
    return PreparedUpload(raw=raw, file_hash=file_hash, parsed=parsed)


def perform_upload(
    prepared: PreparedUpload,
    client: LandingZoneClient,
    is_duplicate_of_bronze: Callable[[str], bool],
    *,
    snapshot_ts: datetime | None = None,
) -> UploadResult:
    """Land a prepared file in MinIO and describe what will happen next."""
    snapshot_ts = snapshot_ts or utcnow()
    duplicate = is_duplicate_of_bronze(prepared.file_hash)
    landed = client.put_export(prepared.raw, prepared.file_hash, snapshot_ts)

    if duplicate:
        message = (
            "Duplicate content — this exact file is already in Bronze, so no "
            "new dataset will be created. The object was still uploaded to "
            "MinIO to keep the upload audit trail complete."
        )
        logger.info("%s (md5=%s, key=%s)", message, prepared.file_hash, landed.key)
    else:
        message = (
            f"New content landed as {landed.key}. Trigger the ingestion DAG "
            "from the Job Management tab to load it into Bronze."
        )
        logger.info(message)

    return UploadResult(
        object_key=landed.key,
        file_hash=prepared.file_hash,
        snapshot_ts=snapshot_ts,
        row_count=prepared.row_count,
        is_duplicate_of_bronze=duplicate,
        message=message,
    )
