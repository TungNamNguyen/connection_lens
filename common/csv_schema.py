"""LinkedIn "Connections" export schema: detection, validation, parsing.

Two rules from the spec drive everything here:

* The number of note lines before the real header row is **not constant**
  across export versions, so the header row is located dynamically — never a
  hardcoded ``skiprows`` (§7, §16, §17).
* A schema change must **fail loudly**, never silently coerce or drop
  columns (§17).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pandas as pd

from common.errors import CsvSchemaError

#: Columns Layer-1 validation insists on before anything is hashed or uploaded (§7).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "First Name",
    "Last Name",
    "URL",
    "Company",
    "Position",
    "Connected On",
)

#: Every column a known-good export may contain. ``Email Address`` is optional
#: content-wise (mostly blank by design, §16) but is part of the known schema.
KNOWN_COLUMNS: tuple[str, ...] = (
    "First Name",
    "Last Name",
    "URL",
    "Email Address",
    "Company",
    "Position",
    "Connected On",
)

#: The marker that identifies the real header row inside the export.
HEADER_MARKER: str = "first name,last name"

_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "latin-1")


@dataclass(frozen=True)
class SchemaValidationResult:
    """Outcome of comparing an export's header against the known schema."""

    columns: tuple[str, ...]
    missing_columns: tuple[str, ...] = ()
    unexpected_columns: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.missing_columns and not self.unexpected_columns

    @property
    def message(self) -> str:
        if self.is_valid:
            return "Header matches the expected LinkedIn export schema."
        problems: list[str] = []
        if self.missing_columns:
            problems.append(f"missing required column(s): {', '.join(self.missing_columns)}")
        if self.unexpected_columns:
            problems.append(
                "unknown column(s) not present in the known export schema: "
                f"{', '.join(self.unexpected_columns)}"
            )
        return (
            "LinkedIn export schema mismatch — "
            + "; ".join(problems)
            + f". Expected exactly: {', '.join(KNOWN_COLUMNS)}."
        )


@dataclass(frozen=True)
class ParsedExport:
    """A successfully parsed export plus the provenance of how it was read."""

    frame: pd.DataFrame
    header_line_index: int
    encoding: str
    note_lines: tuple[str, ...] = field(default=())

    @property
    def row_count(self) -> int:
        return len(self.frame)


def decode_export(raw: bytes) -> tuple[str, str]:
    """Decode raw export bytes, returning ``(text, encoding_used)``.

    UTF-8 first (company/position fields carry Vietnamese diacritics), then
    ``utf-8-sig`` for BOM'd files, then a last-resort ``latin-1`` (§7).
    """
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise CsvSchemaError(
        "Could not decode the uploaded file with any of: " + ", ".join(_ENCODINGS)
    )


def _normalise_header_line(line: str) -> str:
    """Lower-case a line and squeeze whitespace around commas for marker matching."""
    stripped = line.lstrip("﻿").strip().strip('"')
    return ",".join(part.strip().strip('"').lower() for part in stripped.split(","))


def detect_header_line_index(text: str) -> int:
    """Return the 0-based index of the real header line inside the export.

    Scans for the line beginning with ``First Name,Last Name``. Raises
    :class:`CsvSchemaError` when no such line exists — hardcoding a
    ``skiprows`` count is explicitly forbidden (§17).
    """
    for index, line in enumerate(text.splitlines()):
        if _normalise_header_line(line).startswith(HEADER_MARKER):
            return index
    raise CsvSchemaError(
        "Could not locate the export header row: no line starts with "
        f"'{HEADER_MARKER.title()}'. This does not look like a LinkedIn "
        "'Connections' export."
    )


def validate_header(columns: object) -> SchemaValidationResult:
    """Compare an export header against the known schema.

    Missing *required* columns and *unknown* columns are both reported; either
    one makes the result invalid so the caller can fail loudly.
    """
    normalised = tuple(str(column).strip() for column in columns)  # type: ignore[union-attr]
    present = {column.lower() for column in normalised}
    missing = tuple(
        column for column in REQUIRED_COLUMNS if column.lower() not in present
    )
    known = {column.lower() for column in KNOWN_COLUMNS}
    unexpected = tuple(
        column for column in normalised if column and column.lower() not in known
    )
    return SchemaValidationResult(
        columns=normalised, missing_columns=missing, unexpected_columns=unexpected
    )


def snake_case(column: str) -> str:
    """Convert an export column name to the snake_case used in Bronze."""
    cleaned = "".join(
        character if character.isalnum() else " " for character in column.strip()
    )
    return "_".join(part.lower() for part in cleaned.split())


def parse_export(raw: bytes, *, validate: bool = True) -> ParsedExport:
    """Parse raw export bytes into a Bronze-shaped DataFrame.

    Column names are snake_cased; **values stay untouched strings** — cleaning,
    type casting and date parsing belong to Silver, not to ingestion (§7, §18).
    """
    text, encoding = decode_export(raw)
    header_index = detect_header_line_index(text)
    lines = text.splitlines(keepends=True)
    note_lines = tuple(line.rstrip("\r\n") for line in lines[:header_index])

    frame = pd.read_csv(
        io.StringIO("".join(lines[header_index:])),
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )

    if validate:
        result = validate_header(frame.columns)
        if not result.is_valid:
            raise CsvSchemaError(result.message)

    frame.columns = [snake_case(column) for column in frame.columns]
    frame = frame.apply(lambda column: column.str.strip())
    return ParsedExport(
        frame=frame,
        header_line_index=header_index,
        encoding=encoding,
        note_lines=note_lines,
    )
