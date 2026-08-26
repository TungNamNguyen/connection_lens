"""Referral strength scoring (§9, §15).

The score measures how strongly a connection could refer you into the company
they already work at. There is no target company to configure — a person's
referral power belongs to their employer, and filtering by company does the
rest.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from streamlit_app.scoring import (
    DEFAULT_WEIGHTS,
    ReferralWeights,
    has_seniority_signal,
    months_since,
    score_connections,
    score_referral,
)

TODAY = date(2026, 8, 27)
RECENT = "2026-07-01"
OLD = "2023-01-01"


def score(position: str | None, **kwargs: object) -> object:
    """Score someone at a named employer, with sensible defaults."""
    return score_referral(
        company=kwargs.pop("company", "Globex"),
        position=position,
        connected_on=kwargs.pop("connected_on", OLD),
        has_email=bool(kwargs.pop("has_email", False)),
        today=TODAY,
        **kwargs,  # type: ignore[arg-type]
    )


# --- the score needs no configuration -------------------------------------
def test_a_recruiter_ranks_above_an_untagged_title() -> None:
    """Open the tab and the ranking already means something."""
    assert score("Technical Recruiter").total > score("Graphic Designer").total


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Technical Recruiter", DEFAULT_WEIGHTS.role_recruiter),
        ("Chief Technology Officer", DEFAULT_WEIGHTS.role_executive),
        ("Engineering Manager", DEFAULT_WEIGHTS.role_leadership),
        ("Data Scientist", DEFAULT_WEIGHTS.role_peer),
        ("Software Engineer", DEFAULT_WEIGHTS.role_engineering),
    ],
)
def test_each_role_tag_has_its_own_weight(position: str, expected: int) -> None:
    assert score(position).components["role"] == expected


def test_only_the_strongest_role_tag_scores() -> None:
    """A Director of Analytics is leadership + peer, not the sum of both."""
    breakdown = score("Director of Analytics")
    assert breakdown.components["role"] == DEFAULT_WEIGHTS.role_leadership
    assert breakdown.components["second_role"] == DEFAULT_WEIGHTS.additional_role_tag


def test_an_untagged_title_earns_no_role_points() -> None:
    assert "role" not in score("Graphic Designer").components


# --- employer is the whole point ------------------------------------------
@pytest.mark.parametrize("company", [None, "", "   "])
def test_no_employer_means_nowhere_to_refer_you(company: str | None) -> None:
    breakdown = score("Head of Talent Acquisition", company=company)
    assert breakdown.total == 0
    assert breakdown.reason_text == "no employer in the export"


# --- early career subtracts -----------------------------------------------
def test_early_career_is_a_penalty_not_a_bonus() -> None:
    intern = score("Data Engineer Intern", connected_on=RECENT)
    assert intern.components["early_career"] == -DEFAULT_WEIGHTS.early_career_penalty
    assert intern.total < score("Data Engineer", connected_on=RECENT).total


def test_a_score_never_goes_negative() -> None:
    assert score("Junior Intern Student").total == 0


# --- relationship warmth ---------------------------------------------------
def test_a_recent_connection_scores_and_an_old_one_does_not() -> None:
    assert "recent_connection" in score("Data Analyst", connected_on=RECENT).components
    assert "recent_connection" not in score("Data Analyst", connected_on=OLD).components


def test_a_missing_connection_date_is_handled() -> None:
    for value in (None, float("nan"), pd.NaT):
        assert "recent_connection" not in score("Data Analyst", connected_on=value).components


def test_months_since_reads_several_date_types() -> None:
    assert months_since("2026-08-27", TODAY) == pytest.approx(0, abs=0.1)
    assert months_since(date(2025, 8, 27), TODAY) == pytest.approx(12, abs=0.3)
    assert months_since(None, TODAY) is None


def test_an_email_makes_someone_reachable() -> None:
    assert score("Data Analyst", has_email=True).components["email"] == (
        DEFAULT_WEIGHTS.reachable_by_email
    )


# --- what is deliberately absent ------------------------------------------
def test_neither_job_changes_nor_a_target_company_are_scored() -> None:
    """Both were removed on purpose — see the module docstring."""
    assert not hasattr(DEFAULT_WEIGHTS, "recent_change")
    assert not hasattr(DEFAULT_WEIGHTS, "target_company_exact")
    components = score("Data Analyst", connected_on=RECENT).components
    assert "days_since_change" not in components
    assert "target_company" not in components


# --- scale and tuning ------------------------------------------------------
def test_the_strongest_connection_reaches_the_maximum() -> None:
    breakdown = score(
        "Senior Head of Talent Acquisition", connected_on=RECENT, has_email=True
    )
    assert breakdown.total == DEFAULT_WEIGHTS.maximum == 100


def test_weights_are_tunable_without_touching_the_logic() -> None:
    weights = ReferralWeights(role_recruiter=1, seniority=0, recent_connection=0)
    breakdown = score_referral(
        company="Globex", position="Senior Recruiter", weights=weights, today=TODAY
    )
    assert breakdown.total == 1


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Senior Data Engineer", True),
        ("Head of Analytics", True),
        ("Principal Architect", True),
        ("Data Analyst", False),
        (None, False),
    ],
)
def test_seniority_signal(position: str | None, expected: bool) -> None:
    assert has_seniority_signal(position) is expected


# --- frame helper ----------------------------------------------------------
def test_score_connections_adds_columns_and_reasons() -> None:
    frame = pd.DataFrame(
        {
            "company": ["Acme Bank", "Globex", None],
            "position": ["Technical Recruiter", "Graphic Designer", "Head of Talent"],
            "connected_on": [RECENT, OLD, RECENT],
            "email_address": [None, None, None],
        }
    )
    scored = score_connections(frame, today=TODAY)
    assert list(scored["score"]) == [60, 0, 0]
    assert scored.loc[0, "score_reason"] != "—"
    assert scored.loc[2, "score_reason"] == "no employer in the export"


def test_score_connections_handles_an_empty_frame() -> None:
    empty = pd.DataFrame(columns=["company", "position", "connected_on", "email_address"])
    scored = score_connections(empty)
    assert scored.empty
    assert "score" in scored.columns
