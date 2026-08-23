"""Header detection, schema validation and parsing (§7, §16, §17)."""

from __future__ import annotations

import pytest

from common.csv_schema import (
    KNOWN_COLUMNS,
    REQUIRED_COLUMNS,
    decode_export,
    detect_header_line_index,
    parse_export,
    snake_case,
    validate_header,
)
from common.errors import CsvSchemaError


def test_header_row_is_detected_dynamically(export_v1: bytes, export_v2: bytes) -> None:
    """The two fixtures carry a different number of note lines on purpose."""
    text_v1, _ = decode_export(export_v1)
    text_v2, _ = decode_export(export_v2)
    assert detect_header_line_index(text_v1) == 3
    assert detect_header_line_index(text_v2) == 4


def test_header_detection_survives_extra_note_lines(export_v1: bytes) -> None:
    text, _ = decode_export(export_v1)
    padded = "Extra note\nAnother note\n\n" + text
    assert detect_header_line_index(padded) == detect_header_line_index(text) + 3


def test_header_detection_handles_a_bom() -> None:
    text = "﻿Notes:\n\nFirst Name,Last Name,URL,Company,Position,Connected On\n"
    assert detect_header_line_index(text) == 2


def test_missing_header_fails_loudly() -> None:
    with pytest.raises(CsvSchemaError, match="Could not locate the export header row"):
        detect_header_line_index("some,other,csv\n1,2,3\n")


def test_validate_header_accepts_the_known_schema() -> None:
    result = validate_header(KNOWN_COLUMNS)
    assert result.is_valid
    assert not result.missing_columns and not result.unexpected_columns


def test_validate_header_reports_a_missing_required_column() -> None:
    columns = [column for column in KNOWN_COLUMNS if column != "Position"]
    result = validate_header(columns)
    assert not result.is_valid
    assert result.missing_columns == ("Position",)
    assert "Position" in result.message


def test_email_address_is_not_required() -> None:
    """Email is opt-in and mostly blank — it is never a required column (§5)."""
    assert "Email Address" not in REQUIRED_COLUMNS
    columns = [column for column in KNOWN_COLUMNS if column != "Email Address"]
    assert not validate_header(columns).missing_columns


def test_unknown_column_is_reported_rather_than_dropped() -> None:
    result = validate_header([*KNOWN_COLUMNS, "Mystery Score"])
    assert not result.is_valid
    assert result.unexpected_columns == ("Mystery Score",)


def test_parse_export_snake_cases_columns_and_keeps_values_raw(export_v1: bytes) -> None:
    parsed = parse_export(export_v1)
    assert list(parsed.frame.columns) == [
        "first_name",
        "last_name",
        "url",
        "email_address",
        "company",
        "position",
        "connected_on",
    ]
    # Dates stay strings in Bronze; parsing belongs to Silver.
    assert parsed.frame["connected_on"].iloc[0] == "04 Feb 2025"


def test_parse_export_keeps_quoted_commas_intact(export_v1: bytes) -> None:
    frame = parse_export(export_v1).frame
    assert "Sierra, Ph.D" in frame["first_name"].tolist()
    assert "Senior Analytics Engineer, Tech Lead" in frame["position"].tolist()


def test_parse_export_preserves_vietnamese_diacritics(export_v1: bytes) -> None:
    frame = parse_export(export_v1).frame
    assert "Ngân hàng TMCP Ví Dụ" in frame["company"].tolist()


def test_parse_export_keeps_percent_encoded_urls(export_v1: bytes) -> None:
    frame = parse_export(export_v1).frame
    assert any("%E1%BA%AB" in url for url in frame["url"])


def test_parse_export_keeps_blank_emails_and_restricted_rows(export_v1: bytes) -> None:
    frame = parse_export(export_v1).frame
    assert (frame["email_address"] == "").sum() >= 9
    assert (frame["url"] == "").sum() == 1


def test_parse_export_rejects_a_missing_required_column(
    export_missing_column: bytes,
) -> None:
    with pytest.raises(CsvSchemaError, match="Position"):
        parse_export(export_missing_column)


def test_parse_export_decodes_a_bom_prefixed_file(export_v1: bytes) -> None:
    parsed = parse_export(b"\xef\xbb\xbf" + export_v1)
    assert parsed.encoding == "utf-8"
    assert parsed.row_count == 11


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("First Name", "first_name"),
        ("Email Address", "email_address"),
        ("Connected On", "connected_on"),
        ("URL", "url"),
    ],
)
def test_snake_case(raw: str, expected: str) -> None:
    assert snake_case(raw) == expected
