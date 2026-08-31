"""Explicit error types.

Connection Lens fails loudly: a schema change, an unparsable object key or a
malformed export must surface as an exception with an actionable message,
never as a silently coerced/dropped column (§17).
"""

from __future__ import annotations


class ConnectionLensError(Exception):
    """Base class for every error raised by this project."""


class CsvSchemaError(ConnectionLensError):
    """The uploaded/ingested CSV does not match the expected LinkedIn export schema."""


class ObjectKeyError(ConnectionLensError):
    """A MinIO object key does not follow the documented landing-zone convention."""


class IngestionError(ConnectionLensError):
    """Bronze ingestion could not complete."""


class LandingZoneError(ConnectionLensError):
    """MinIO could not be reached, or refused an operation.

    Exists so the SDK's own exception types (`minio.error.S3Error`,
    `urllib3.exceptions.MaxRetryError`) never reach the Streamlit pages, which
    only know how to handle a :class:`ConnectionLensError`.
    """


class WarehouseNotReadyError(ConnectionLensError):
    """The DuckDB warehouse file does not exist yet (no ingestion has run)."""


class WarehouseBusyError(ConnectionLensError):
    """The warehouse is locked by its single writer — a DAG run is in progress.

    DuckDB allows *either* one read-write process *or* several read-only ones,
    never both at once (§10), so every read from Streamlit fails for as long as
    an ingestion run holds the file. That is a transient, expected state rather
    than a fault: callers must say so and let the reader retry, never crash.
    """
