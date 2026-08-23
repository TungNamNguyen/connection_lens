"""Warm-intro scoring (§9, §15)."""

from __future__ import annotations

import pandas as pd
import pytest

from streamlit_app.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    company_match,
    has_seniority_signal,
    normalise_company_name,
    score_connection,
    score_connections,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Bank (ACMB)", "acme bank acmb"),
        ("Example Corporation", "example"),
        ("Công ty TNHH Example", "example"),
        ("  Globex   ", "globex"),
        (None, ""),
        ("", ""),
    ],
)
def test_company_normalisation(raw: str | None, expected: str) -> None:
    assert normalise_company_name(raw) == expected


@pytest.mark.parametrize(
    ("company", "target", "expected"),
    [
        ("Example Corporation", "Example", "exact"),
        ("Example", "example corporation", "exact"),
        ("Acme Bank (ACMB)", "Acme Bank", "partial"),
        ("Globex", "Acme Shop", "none"),
        ("Acme Software", "Acme Retail", "none"),
        (None, "Example", "none"),
        ("Example", None, "none"),
    ],
)
def test_company_match(company: str | None, target: str | None, expected: str) -> None:
    assert company_match(company, target) == expected


def test_no_target_company_means_no_score() -> None:
    """The table is sorted by recency instead until a target is entered (§9)."""
    breakdown = score_connection(
        company="Example", position="CTO", days_since_change=1, target_company=""
    )
    assert breakdown.total == 0
    assert breakdown.components == {}


def test_full_house_scores_every_component() -> None:
    breakdown = score_connection(
        company="Example Corporation",
        position="Senior Engineering Manager",
        days_since_change=12,
        target_company="Example",
    )
    assert breakdown.components == {
        "company_exact_match": DEFAULT_WEIGHTS.company_exact_match,
        "recent_change": DEFAULT_WEIGHTS.recent_change,
        "seniority": DEFAULT_WEIGHTS.seniority,
    }
    assert breakdown.total == 85
    assert "works at Example Corporation" in breakdown.reason_text


def test_partial_company_match_scores_half() -> None:
    breakdown = score_connection(
        company="Acme Bank (ACMB)",
        position="Data Analyst",
        days_since_change=None,
        target_company="Acme Bank",
    )
    assert breakdown.total == DEFAULT_WEIGHTS.company_partial_match


def test_a_change_outside_the_window_scores_nothing() -> None:
    breakdown = score_connection(
        company="Globex",
        position="Data Analyst",
        days_since_change=DEFAULT_WEIGHTS.recent_change_window_days + 1,
        target_company="Globex",
    )
    assert "recent_change" not in breakdown.components


def test_missing_recency_is_handled() -> None:
    for value in (None, float("nan")):
        breakdown = score_connection(
            company="Globex",
            position="Data Analyst",
            days_since_change=value,
            target_company="Globex",
        )
        assert breakdown.total == DEFAULT_WEIGHTS.company_exact_match


def test_weights_are_tunable_without_touching_the_logic() -> None:
    weights = ScoringWeights(company_exact_match=10, recent_change=1, seniority=0)
    breakdown = score_connection(
        company="Globex",
        position="Head of Data",
        days_since_change=5,
        target_company="Globex",
        weights=weights,
    )
    assert breakdown.total == 11


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Senior Data Engineer", True),
        ("Head of Analytics", True),
        ("Chief Data Officer", True),
        ("Staff Engineer", True),
        ("Data Analyst", False),
        (None, False),
    ],
)
def test_seniority_signal(position: str | None, expected: bool) -> None:
    assert has_seniority_signal(position) is expected


def test_score_connections_adds_columns_and_reasons() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Example Corporation", "Acme Shop"],
            "position": ["Data Scientist", "Junior Graphic Designer"],
            "days_since_change": [10, 900],
        }
    )
    scored = score_connections(frame, "Example")
    assert list(scored["score"]) == [70, 0]
    assert scored.loc[0, "score_reason"] != "—"
    assert scored.loc[1, "score_reason"] == "—"


def test_score_connections_handles_an_empty_frame() -> None:
    empty = pd.DataFrame(columns=["company", "position", "days_since_change"])
    scored = score_connections(empty, "Example")
    assert scored.empty
    assert "score" in scored.columns
