"""Referral strength scoring (§9, §15).

The score measures how strongly a connection could refer you into a role you
actually apply for, at the company they already work at. There is no target
company to configure — a person's referral power belongs to their employer,
and filtering by company does the rest.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from streamlit_app.scoring import (
    DEFAULT_WEIGHTS,
    PATH_ADJACENT_LEADER,
    PATH_ADJACENT_PEER,
    PATH_FIELD_LEADER,
    PATH_FIELD_PEER,
    PATH_INHOUSE_RECRUITER,
    PATH_NO_EMPLOYER,
    PATH_NONE,
    PATH_OUTSIDE_LEADER,
    PATH_OUTSIDE_RECRUITER,
    SIGNAL_LABELS,
    ReferralWeights,
    company_data_teams,
    has_seniority_signal,
    months_since,
    path_frequency,
    referral_path,
    score_connections,
    score_referral,
    signal_frequency,
)

TODAY = date(2026, 8, 27)
RECENT = "2026-07-01"
OLD = "2019-01-01"


def score(position: str | None, **kwargs: object) -> object:
    """Score someone at a named employer, with sensible defaults."""
    return score_referral(
        company=kwargs.pop("company", "Globex"),
        position=position,
        connected_on=kwargs.pop("connected_on", OLD),
        today=TODAY,
        **kwargs,  # type: ignore[arg-type]
    )


# --- one person, one way in ------------------------------------------------
@pytest.mark.parametrize(
    ("position", "has_team", "expected"),
    [
        ("Head of Data Engineering", False, PATH_FIELD_LEADER),
        # No family rule claims "Head of Data"; the target_peer tag does, and
        # they can still sign a headcount.
        ("Head of Data", False, PATH_FIELD_LEADER),
        ("Chief Data Officer", False, PATH_FIELD_LEADER),
        ("Business Intelligence Analyst", False, PATH_FIELD_PEER),
        ("Analytics Engineer", False, PATH_FIELD_PEER),
        ("Technical Recruiter", True, PATH_INHOUSE_RECRUITER),
        ("Technical Recruiter", False, PATH_OUTSIDE_RECRUITER),
        ("Engineering Manager", False, PATH_ADJACENT_LEADER),
        ("Backend Developer", False, PATH_ADJACENT_PEER),
        ("Sales Director", False, PATH_OUTSIDE_LEADER),
        ("Graphic Designer", False, PATH_NONE),
    ],
)
def test_each_title_gets_exactly_one_referral_path(
    position: str, has_team: bool, expected: str
) -> None:
    assert referral_path(position, company_has_data_team=has_team) == expected


def test_the_paths_are_ordered_by_how_directly_they_lead_in() -> None:
    """A leader in your field outranks a peer, who outranks a recruiter."""
    points = DEFAULT_WEIGHTS.base_points
    assert (
        points[PATH_FIELD_LEADER]
        > points[PATH_FIELD_PEER]
        > points[PATH_INHOUSE_RECRUITER]
        > points[PATH_ADJACENT_LEADER]
        > points[PATH_ADJACENT_PEER]
        > points[PATH_OUTSIDE_RECRUITER]
        > points[PATH_OUTSIDE_LEADER]
        > points[PATH_NONE]
    )


def test_relevance_beats_seniority() -> None:
    """The whole point of the rewrite: a founder elsewhere is not a way in."""
    assert score("Data Analyst").total > score("Founder & CEO").total


def test_an_in_house_recruiter_outranks_an_agency_one() -> None:
    in_house = score("Technical Recruiter", company_has_data_team=True)
    agency = score("Technical Recruiter", company_has_data_team=False)
    assert in_house.total > agency.total
    assert "no data team" in agency.reason_text


# --- no route means no score -----------------------------------------------
@pytest.mark.parametrize("company", ["", "   ", None])
def test_no_employer_means_nowhere_to_refer_you(company: str | None) -> None:
    breakdown = score("Head of Data", company=company, connected_on=RECENT)
    assert breakdown.total == 0
    assert breakdown.path == PATH_NO_EMPLOYER


def test_warmth_cannot_invent_a_route_that_is_not_there() -> None:
    """Someone unrelated stays at zero however recently you connected."""
    breakdown = score("Graphic Designer", connected_on=RECENT)
    assert breakdown.total == 0
    assert breakdown.path == PATH_NONE
    assert breakdown.components == {}


def test_a_score_never_goes_negative() -> None:
    weights = ReferralWeights(early_career_penalty=1_000)
    breakdown = score_referral(
        company="Globex", position="Junior Data Analyst", weights=weights, today=TODAY
    )
    assert breakdown.total == 0


# --- modifiers --------------------------------------------------------------
def test_a_recent_connection_scores_and_an_old_one_fades() -> None:
    recent = score("Data Analyst", connected_on=RECENT)
    old = score("Data Analyst", connected_on=OLD)
    assert recent.components["warmth"] > old.components.get("warmth", 0)
    assert recent.total > old.total


def test_warmth_decays_smoothly_rather_than_falling_off_a_cliff() -> None:
    """Continuous decay is what stops the scale collapsing into ties."""
    scores = [
        score("Data Analyst", connected_on=f"2026-0{month}-01").total
        for month in range(1, 8)
    ]
    assert scores == sorted(scores)
    assert len(set(scores)) > 1


def test_a_missing_connection_date_is_handled() -> None:
    breakdown = score("Data Analyst", connected_on=None)
    assert "warmth" not in breakdown.components
    assert breakdown.total == DEFAULT_WEIGHTS.field_peer


def test_leaders_do_not_collect_seniority_twice() -> None:
    """Their base already prices in what "Head of" says about them."""
    leader = score("Head of Data Engineering")
    assert "seniority" not in leader.components
    peer = score("Senior Data Engineer")
    assert peer.components["seniority"] == DEFAULT_WEIGHTS.seniority


def test_a_recent_job_change_scores() -> None:
    moved = score("Data Analyst", changed_at="2026-07-15", has_previous_version=True)
    settled = score("Data Analyst", changed_at="2024-01-01", has_previous_version=True)
    assert moved.components["recent_move"] == DEFAULT_WEIGHTS.recent_move
    assert "recent_move" not in settled.components


def test_a_first_ingestion_does_not_announce_a_job_change_for_everyone() -> None:
    """Every row is new after the first import; only a closed older version
    proves somebody actually moved."""
    first_import = score("Data Analyst", changed_at="2026-08-20")
    assert "recent_move" not in first_import.components
    assert "changed job recently" not in first_import.reason_text


def test_early_career_is_a_penalty_not_a_bonus() -> None:
    junior = score("Junior Data Analyst")
    mid = score("Data Analyst")
    assert junior.components["early_career"] < 0
    assert junior.total < mid.total


def test_months_since_reads_several_date_types() -> None:
    for value in ("2026-07-27", date(2026, 7, 27), pd.Timestamp("2026-07-27")):
        assert months_since(value, TODAY) == pytest.approx(1.0, abs=0.1)
    assert months_since(None, TODAY) is None
    assert months_since("not a date", TODAY) is None


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


# --- what is deliberately not scored ---------------------------------------
def test_an_email_is_not_scored() -> None:
    """It is the connection's privacy setting, not their willingness to help."""
    assert not hasattr(DEFAULT_WEIGHTS, "reachable_by_email")
    assert "email" not in SIGNAL_LABELS


def test_a_target_company_is_not_scored() -> None:
    assert not hasattr(DEFAULT_WEIGHTS, "target_company_exact")
    assert "target_company" not in score("Data Analyst").components


def test_how_many_people_you_know_there_is_not_scored() -> None:
    """That is the Companies tab; counting it here would rank big employers."""
    assert "company_size" not in score("Data Analyst").components
    assert "company_reach" not in SIGNAL_LABELS


# --- scale and tuning ------------------------------------------------------
def test_the_strongest_connection_reaches_the_maximum() -> None:
    breakdown = score(
        "Head of Data Engineering",
        connected_on="2026-08-27",
        changed_at="2026-08-01",
        has_previous_version=True,
    )
    assert breakdown.total == DEFAULT_WEIGHTS.maximum == 75


def test_weights_are_tunable_without_touching_the_logic() -> None:
    weights = ReferralWeights(field_peer=1, seniority=0, warmth=0)
    breakdown = score_referral(
        company="Globex", position="Data Analyst", weights=weights, today=TODAY
    )
    assert breakdown.total == 1


# --- frame helpers ----------------------------------------------------------
FRAME = pd.DataFrame(
    {
        "company": ["Acme", "Acme", "Globex", None],
        "company_key": ["acme", "acme", "globex", None],
        "position": [
            "Head of Data Engineering",
            "Technical Recruiter",
            "Graphic Designer",
            "Data Analyst",
        ],
        "connected_on": [RECENT, RECENT, RECENT, RECENT],
        "dbt_valid_from": [None, None, None, None],
    }
)


def test_score_connections_adds_columns_and_reasons() -> None:
    scored = score_connections(FRAME, today=TODAY)
    assert scored.loc[0, "score"] > scored.loc[1, "score"] > scored.loc[2, "score"]
    assert scored.loc[0, "referral_path"] == PATH_FIELD_LEADER
    # The recruiter shares an employer with a data leader, so they are in-house.
    assert scored.loc[1, "referral_path"] == PATH_INHOUSE_RECRUITER
    assert scored.loc[2, "score"] == 0
    assert scored.loc[3, "referral_path"] == PATH_NO_EMPLOYER
    assert "can hire in your field" in scored.loc[0, "score_reason"]


def test_company_data_teams_reads_the_whole_network() -> None:
    """Whether a company has a data team cannot depend on the reader's filter."""
    keys = company_data_teams(FRAME)
    assert keys == {"acme"}
    # Filtering the recruiter's employer down to just the recruiter must not
    # silently turn them into an agency recruiter.
    only_recruiter = FRAME.iloc[[1]]
    scored = score_connections(
        only_recruiter.assign(company_has_data_team=only_recruiter["company_key"].isin(keys)),
        today=TODAY,
    )
    assert scored.iloc[0]["referral_path"] == PATH_INHOUSE_RECRUITER


def test_score_connections_handles_an_empty_frame() -> None:
    scored = score_connections(pd.DataFrame(), today=TODAY)
    assert scored.empty
    assert {"score", "score_reason", "referral_path"} <= set(scored.columns)


def test_signal_frequency_counts_connections_not_points() -> None:
    frequency = signal_frequency(FRAME, today=TODAY).set_index("signal")
    assert frequency.loc[SIGNAL_LABELS["role"], "connections"] == 2
    assert frequency.loc[SIGNAL_LABELS["warmth"], "connections"] == 2


def test_signal_frequency_reports_every_signal_even_at_zero() -> None:
    frequency = signal_frequency(FRAME, today=TODAY)
    assert list(frequency["signal"]) == list(SIGNAL_LABELS.values())
    assert (frequency["connections"] >= 0).all()


def test_signal_frequency_handles_an_empty_frame() -> None:
    assert signal_frequency(pd.DataFrame(), today=TODAY).empty


def test_path_frequency_describes_what_the_network_is_made_of() -> None:
    frequency = path_frequency(score_connections(FRAME, today=TODAY)).set_index("path")
    assert frequency.loc["can hire in your field", "connections"] == 1
    assert frequency.loc["no referral path", "connections"] == 1
    assert frequency.loc["no employer in the export", "connections"] == 1


def test_path_frequency_handles_an_unscored_frame() -> None:
    assert path_frequency(pd.DataFrame()).empty


def test_ties_are_expected_and_meant_to_be_broken_downstream() -> None:
    """Whole-number scores tie by design; the page breaks ties on recency.

    This test pins the contract: `score` is coarse on purpose (a readable
    number with a stated maximum), so any ranking built on it must supply its
    own second key rather than falling back on alphabetical order.
    """
    same_score = [
        score("Data Analyst", connected_on="2026-08-05").total,
        score("Business Intelligence Analyst", connected_on="2026-08-05").total,
    ]
    assert same_score[0] == same_score[1]
