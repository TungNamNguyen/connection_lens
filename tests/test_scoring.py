"""Referral strength scoring (§9, §15).

The score measures how strong a referral a connection could give. It is a
property of the person, so it works with or without a target company.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from streamlit_app.scoring import (
    DEFAULT_WEIGHTS,
    ReferralWeights,
    company_aliases,
    company_match,
    has_seniority_signal,
    months_since,
    normalise_company_name,
    score_connections,
    score_referral,
)

TODAY = date(2026, 8, 26)


# --- company matching ------------------------------------------------------
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
        ("Acme Bank (ACMB)", "Acme Bank", "exact"),
        ("Globex", "Acme Shop", "none"),
        ("Acme Software", "Acme Retail", "none"),
        (None, "Example", "none"),
        ("Example", None, "none"),
    ],
)
def test_company_match(company: str | None, target: str | None, expected: str) -> None:
    assert company_match(company, target) == expected


# --- the score works without a target -------------------------------------
def test_a_recruiter_scores_without_any_target_company() -> None:
    """The whole point of the redesign: ranking works before you pick a target."""
    breakdown = score_referral(
        company="Globex",
        position="Senior Technical Recruiter",
        connected_on="2026-05-01",
        has_email=True,
        today=TODAY,
    )
    assert breakdown.components == {
        "role": DEFAULT_WEIGHTS.role_recruiter,
        "seniority": DEFAULT_WEIGHTS.seniority,
        "recent_connection": DEFAULT_WEIGHTS.recent_connection,
        "email": DEFAULT_WEIGHTS.reachable_by_email,
    }
    assert breakdown.total == 50


def test_naming_the_target_company_adds_the_largest_term() -> None:
    without = score_referral(
        company="Acme Bank", position="Data Analyst", today=TODAY
    ).total
    with_target = score_referral(
        company="Acme Bank", position="Data Analyst", target_company="Acme Bank",
        today=TODAY,
    ).total
    assert with_target - without == DEFAULT_WEIGHTS.target_company_exact


def test_an_unrelated_company_never_earns_the_target_term() -> None:
    breakdown = score_referral(
        company="Globex", position="Data Analyst", target_company="Acme Bank",
        today=TODAY,
    )
    assert "target_company" not in breakdown.components


def test_a_partial_company_match_scores_less() -> None:
    """A bare token inside a longer name is a hint, not a confirmation."""
    breakdown = score_referral(
        company="Acme Bank (ACMB)", position="Data Analyst",
        target_company="acme", today=TODAY,
    )
    assert breakdown.components["target_company_partial"] == (
        DEFAULT_WEIGHTS.target_company_partial
    )


# --- role weighting --------------------------------------------------------
def test_only_the_strongest_role_tag_scores() -> None:
    """A Director of Analytics is leadership + peer, not the sum of both."""
    breakdown = score_referral(position="Director of Analytics", company="Globex", today=TODAY)
    assert breakdown.components["role"] == DEFAULT_WEIGHTS.role_leadership
    assert breakdown.components["second_role"] == DEFAULT_WEIGHTS.additional_role_tag


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Technical Recruiter", DEFAULT_WEIGHTS.role_recruiter),
        ("Chief Technology Officer", DEFAULT_WEIGHTS.role_executive),
        ("Engineering Manager", DEFAULT_WEIGHTS.role_leadership),
        ("Data Scientist", DEFAULT_WEIGHTS.role_peer),
    ],
)
def test_each_role_tag_has_its_own_weight(position: str, expected: int) -> None:
    assert score_referral(position=position, company="Globex", today=TODAY).components["role"] == expected


def test_an_untagged_title_earns_no_role_points() -> None:
    breakdown = score_referral(position="Junior Graphic Designer", company="Globex", today=TODAY)
    assert "role" not in breakdown.components


# --- relationship warmth ---------------------------------------------------
def test_an_old_connection_earns_no_warmth_points() -> None:
    breakdown = score_referral(
        position="Data Analyst", company="Globex", connected_on="2023-01-01", today=TODAY
    )
    assert "recent_connection" not in breakdown.components


def test_a_missing_connection_date_is_handled() -> None:
    for value in (None, float("nan"), pd.NaT):
        breakdown = score_referral(
            position="Data Analyst", company="Globex", connected_on=value, today=TODAY
        )
        assert "recent_connection" not in breakdown.components


def test_months_since_reads_several_date_types() -> None:
    assert months_since("2026-08-26", TODAY) == pytest.approx(0, abs=0.1)
    assert months_since(date(2025, 8, 26), TODAY) == pytest.approx(12, abs=0.3)
    assert months_since(None, TODAY) is None


# --- change tracking is deliberately gone ---------------------------------
def test_recency_of_a_job_change_is_not_scored() -> None:
    """It needs several exports to mean anything; until then it ranks nothing."""
    assert not hasattr(DEFAULT_WEIGHTS, "recent_change")
    assert "days_since_change" not in score_referral(
        position="Data Analyst", company="Globex", today=TODAY
    ).components


# --- scale and tuning ------------------------------------------------------
def test_the_perfect_connection_reaches_the_maximum() -> None:
    breakdown = score_referral(
        company="Acme Bank",
        position="Senior Head of Talent Acquisition, Data",
        target_company="Acme Bank",
        connected_on="2026-08-01",
        has_email=True,
        today=TODAY,
    )
    assert breakdown.total == DEFAULT_WEIGHTS.maximum == 100


def test_weights_are_tunable_without_touching_the_logic() -> None:
    weights = ReferralWeights(role_recruiter=1, seniority=0, recent_connection=0)
    breakdown = score_referral(
        position="Senior Recruiter", company="Globex", weights=weights, today=TODAY
    )
    assert breakdown.total == 1


@pytest.mark.parametrize(
    ("position", "expected"),
    [("Senior Data Engineer", True), ("Head of Analytics", True), ("Data Analyst", False), (None, False)],
)
def test_seniority_signal(position: str | None, expected: bool) -> None:
    assert has_seniority_signal(position) is expected


# --- frame helper ----------------------------------------------------------
def test_score_connections_adds_columns_and_reasons() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Acme Bank", "Globex"],
            "position": ["Data Scientist", "Junior Graphic Designer"],
            "connected_on": ["2026-08-01", "2020-01-01"],
            "email_address": [None, None],
        }
    )
    scored = score_connections(frame, "Acme Bank", today=TODAY)
    assert list(scored["score"]) == [70, 0]
    assert scored.loc[0, "score_reason"] != "—"
    assert scored.loc[1, "score_reason"] == "—"


def test_score_connections_without_a_target_still_ranks() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Globex", "Globex"],
            "position": ["Technical Recruiter", "Junior Graphic Designer"],
            "connected_on": ["2026-08-01", "2026-08-01"],
            "email_address": [None, None],
        }
    )
    scored = score_connections(frame, None, today=TODAY)
    assert scored.loc[0, "score"] > scored.loc[1, "score"]


def test_score_connections_handles_an_empty_frame() -> None:
    empty = pd.DataFrame(columns=["company", "position", "connected_on", "email_address"])
    scored = score_connections(empty, "Acme Bank")
    assert scored.empty
    assert "score" in scored.columns


# --- "Brand (Legal entity)" spellings --------------------------------------
@pytest.mark.parametrize(
    ("company", "target", "expected"),
    [
        # LinkedIn writes the legal entity in brackets; searching the brand
        # must still count as working there.
        ("MoMo (M_Service)", "momo", "exact"),
        ("MoMo (M_Service)", "M_Service", "exact"),
        ("Acme Bank (ACMB)", "ACMB", "exact"),
        # A bare token that is merely part of a longer name stays ambiguous.
        ("Acme Bank (ACMB)", "acme", "partial"),
        ("Globex", "MoMo", "none"),
    ],
)
def test_a_bracketed_legal_entity_is_an_alias(
    company: str, target: str, expected: str
) -> None:
    assert company_match(company, target) == expected


def test_company_aliases_lists_both_halves() -> None:
    assert company_aliases("MoMo (M_Service)") == {"momo m_service", "momo", "m_service"}
    assert company_aliases(None) == set()
