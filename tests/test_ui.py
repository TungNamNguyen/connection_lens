"""Presentation helpers (§9).

Only the pure ones are worth testing here; the rest is Streamlit rendering,
covered by the page smoke tests.
"""

from __future__ import annotations

import pytest

from streamlit_app.ui import display_profile_url, fold_accents, format_date, format_duration


# --- accent-blind search ---------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nguyễn", "nguyen"),
        ("Lê Ngọc Thạch", "le ngoc thach"),
        ("Ngân hàng TMCP", "ngan hang tmcp"),
        # "đ" is a letter of its own, so NFD leaves it alone.
        ("Đặng Phương Linh", "dang phuong linh"),
        ("Data Engineer", "data engineer"),
        ("", ""),
        (None, ""),
    ],
)
def test_fold_accents(raw: str | None, expected: str) -> None:
    assert fold_accents(raw) == expected


def test_folding_makes_search_accent_blind() -> None:
    """Typing without diacritics has to find the name that has them."""
    haystack = fold_accents("Nguyễn Văn Mẫu — Senior Data Engineer")
    assert fold_accents("nguyen van mau") in haystack
    assert fold_accents("DATA ENGINEER") in haystack
    assert fold_accents("recruiter") not in haystack


# --- small formatters ------------------------------------------------------
def test_profile_url_is_unquoted_for_display_only() -> None:
    encoded = "https://www.linkedin.com/in/m%E1%BA%ABu-th%E1%BB%AD-0010"
    assert display_profile_url(encoded) == "https://www.linkedin.com/in/mẫu-thử-0010"
    assert display_profile_url(None) == ""


def test_format_date_tolerates_missing_values() -> None:
    assert format_date("2026-08-27") == "2026-08-27"
    assert format_date(None) == "—"
    assert format_date("not a date") == "—"


def test_format_duration() -> None:
    assert format_duration(70) == "1m 10s"
    assert format_duration(9) == "9s"
    assert format_duration(None) == "—"
