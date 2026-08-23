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


class WarehouseNotReadyError(ConnectionLensError):
    """The DuckDB warehouse file does not exist yet (no ingestion has run)."""
