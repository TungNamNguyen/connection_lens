"""Bronze ingestion: MinIO landing zone -> `bronze.raw_connections`.

The whole task is trigger-agnostic (§5, §8): it always scans MinIO for content
hashes that are not yet in Bronze, regardless of which of the three trigger
modes started the DAG run. A redundant trigger is therefore a harmless no-op.

Idempotency is decided **only** by the MD5 of the object's bytes checked
against Bronze — never by date, upload time, or trigger source (§14, §17).
"""

from __future__ import annotations

import logging

import duckdb

from common.csv_schema import parse_export
from common.duckdb_io import (
    append_bronze_batch,
    ensure_bronze,
    fetch_ingested_hashes,
    hash_in_bronze,
)
from common.errors import IngestionError
from common.hashing import md5_bytes, short_hash
from common.minio_client import LandingZoneClient
from common.models import IngestionReport, LandingObject, ObjectIngestionResult

logger = logging.getLogger(__name__)


def select_candidate_objects(
    landing_objects: list[LandingObject], ingested_hashes: set[str]
) -> list[LandingObject]:
    """Narrow the landing zone down to objects that *might* be new.

    Cheap pre-filter on the 8-character hash carried by the object key; the
    authoritative check happens after download against the full MD5.
    """
    ingested_short = {short_hash(value) for value in ingested_hashes}
    return [obj for obj in landing_objects if obj.hash8 not in ingested_short]


def scan_for_pending_objects(
    client: LandingZoneClient, connection: duckdb.DuckDBPyConnection
) -> list[LandingObject]:
    """Return landing-zone objects whose content is not yet in Bronze."""
    ensure_bronze(connection)
    landing_objects = client.list_landing_objects()
    ingested_hashes = fetch_ingested_hashes(connection)
    pending = select_candidate_objects(landing_objects, ingested_hashes)
    logger.info(
        "Landing zone scan: %d object(s) present, %d already in Bronze, "
        "%d candidate(s) pending ingestion.",
        len(landing_objects),
        len(ingested_hashes),
        len(pending),
    )
    return pending


def ingest_object(
    connection: duckdb.DuckDBPyConnection,
    landing_object: LandingObject,
    raw: bytes,
) -> ObjectIngestionResult:
    """Ingest one landing-zone object into Bronze, or skip it as a duplicate.

    Re-checks the full content hash against Bronze itself — defence in depth,
    the app layer is not trusted (§7).
    """
    file_hash = md5_bytes(raw)
    if short_hash(file_hash) != landing_object.hash8:
        raise IngestionError(
            f"Object {landing_object.key!r} claims content hash "
            f"{landing_object.hash8!r} but its bytes hash to "
            f"{short_hash(file_hash)!r}. Refusing to ingest tampered or "
            "mis-named content."
        )

    if hash_in_bronze(connection, file_hash):
        message = (
            f"SKIP duplicate content: {landing_object.key} (md5={file_hash}) is "
            "already in Bronze — no new dataset created."
        )
        logger.info(message)
        return ObjectIngestionResult(
            key=landing_object.key,
            snapshot_ts=landing_object.snapshot_ts,
            file_hash=file_hash,
            status="skipped_duplicate",
            message=message,
        )

    parsed = parse_export(raw)
    rows = append_bronze_batch(
        connection,
        parsed.frame,
        snapshot_ts=landing_object.snapshot_ts,
        file_hash=file_hash,
        source_object=landing_object.key,
    )
    message = (
        f"INGESTED {rows} row(s) from {landing_object.key} "
        f"(md5={file_hash}, header detected on line "
        f"{parsed.header_line_index + 1}, encoding={parsed.encoding})."
    )
    logger.info(message)
    return ObjectIngestionResult(
        key=landing_object.key,
        snapshot_ts=landing_object.snapshot_ts,
        file_hash=file_hash,
        status="ingested",
        rows_ingested=rows,
        message=message,
    )


def ingest_pending_objects(
    connection: duckdb.DuckDBPyConnection,
    client: LandingZoneClient,
    pending: list[LandingObject],
) -> IngestionReport:
    """Ingest every pending object, oldest snapshot first."""
    report = IngestionReport(objects_scanned=len(pending))
    for landing_object in sorted(pending, key=lambda obj: obj.snapshot_ts):
        raw = client.get_object_bytes(landing_object.key)
        report.add(ingest_object(connection, landing_object, raw))
    logger.info("Bronze ingestion finished: %s", report.summary_line)
    return report
