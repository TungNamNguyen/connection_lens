"""Company-level reach: the aggregation behind the Companies tab (§9).

Synthetic rows only — the shapes here are the ones a real export produces, the
people are invented.
"""

from __future__ import annotations

import pandas as pd
import pytest

from streamlit_app.companies import classify, company_reach, summarise
from streamlit_app.tagging import ADJACENT_FAMILIES, TARGET_FAMILIES


def frame(rows: list[tuple[str, str]]) -> pd.DataFrame:
    """Build the two columns `company_reach` reads: employer and job title."""
    return pd.DataFrame(
        [{"company_name": company, "position": position} for company, position in rows]
    )


NETWORK = frame(
    [
        # Acme: a peer in the field, someone who can hire, and a recruiter.
        ("Acme Bank", "Senior Data Engineer"),
        ("Acme Bank", "Head of Data"),
        ("Acme Bank", "Technical Recruiter"),
        ("Acme Bank", "Marketing Specialist"),
        # Globex: two people in the field, no recruiter — no front door.
        ("Globex", "Business Intelligence Analyst"),
        ("Globex", "Analytics Engineer"),
        # Initech: adjacent only.
        ("Initech", "Backend Developer"),
        ("Initech", "Talent Acquisition Partner"),
        # Somewhere you know one unrelated person.
        ("Umbrella", "Graphic Designer"),
    ]
)


def test_reach_counts_every_kind_of_door() -> None:
    reach = company_reach(NETWORK).set_index("company")

    acme = reach.loc["Acme Bank"]
    assert acme["core_peers"] == 1  # the data engineer
    assert acme["data_leaders"] == 1  # Head of Data
    assert acme["recruiters"] == 1
    assert acme["connections"] == 4  # the marketer still counts as someone you know
    # 1×3 (core) + 1×3 (leader) + 1×2 (recruiter) + 1×1 (data person)
    assert acme["reach"] == 9


def test_a_company_without_a_recruiter_has_no_front_door() -> None:
    reach = company_reach(NETWORK).set_index("company")
    assert bool(reach.loc["Acme Bank"]["has_front_door"]) is True
    assert bool(reach.loc["Globex"]["has_front_door"]) is False


def test_leaders_outside_your_field_do_not_count_as_able_to_hire() -> None:
    """A Sales Director leads, but not anywhere that can open a data role."""
    reach = company_reach(frame([("Contoso", "Sales Director")])).set_index("company")
    assert reach.loc["Contoso"]["data_leaders"] == 0
    assert reach.loc["Contoso"]["core_peers"] == 0
    assert reach.loc["Contoso"]["reach"] == 0


def test_ranking_is_by_reach_then_name() -> None:
    reach = company_reach(NETWORK)
    assert list(reach["company"])[:2] == ["Acme Bank", "Globex"]
    assert reach["reach"].is_monotonic_decreasing


def test_connections_without_an_employer_are_excluded() -> None:
    """They cannot be a way into anywhere — Network Stats counts them instead."""
    with_blanks = pd.concat(
        [
            NETWORK,
            frame([("", "Data Engineer")]),
            pd.DataFrame([{"company_name": None, "position": "Data Analyst"}]),
            frame([("(unknown)", "Data Analyst")]),
        ],
        ignore_index=True,
    )
    companies = set(company_reach(with_blanks)["company"])
    assert companies == {"Acme Bank", "Globex", "Initech", "Umbrella"}


def test_target_families_change_the_ranking() -> None:
    """Reach is relative to the roles you are applying for, not absolute."""
    as_data = company_reach(NETWORK).set_index("company")
    as_engineer = company_reach(
        NETWORK,
        target_families=("Software Engineering",),
        adjacent_families=ADJACENT_FAMILIES,
    ).set_index("company")

    assert as_data.loc["Initech"]["core_peers"] == 0
    assert as_engineer.loc["Initech"]["core_peers"] == 1  # the backend developer
    assert as_engineer.loc["Initech"]["reach"] > as_data.loc["Initech"]["reach"]


def test_summarise_counts_the_three_ways_in() -> None:
    totals = summarise(company_reach(NETWORK))
    assert totals["companies"] == 4
    assert totals["with_core"] == 2  # Acme and Globex
    assert totals["all_three"] == 1  # only Acme has peer + leader + recruiter
    assert totals["closed_with_leader"] == 0
    assert totals["front_door"] == 2  # Acme and Initech


def test_a_closed_company_with_a_leader_is_flagged() -> None:
    totals = summarise(company_reach(frame([("Stark", "Head of Analytics")])))
    assert totals["front_door"] == 0
    assert totals["closed_with_leader"] == 1


@pytest.mark.parametrize("empty", [pd.DataFrame(), pd.DataFrame({"position": []})])
def test_an_empty_network_returns_an_empty_table(empty: pd.DataFrame) -> None:
    reach = company_reach(empty)
    assert reach.empty
    assert list(reach.columns)[:2] == ["company", "reach"]
    assert summarise(reach)["companies"] == 0


def test_classify_labels_each_row_once() -> None:
    classified = classify(NETWORK)
    assert len(classified) == len(NETWORK)
    assert classified["family"].notna().all()
    # "Head of Data" matches no job-family rule, so it is not counted as a peer
    # — but it is still someone who can open one of these roles.
    head_of_data = classified[classified["position"] == "Head of Data"].iloc[0]
    assert bool(head_of_data["is_data_leader"]) is True
    assert bool(head_of_data["is_core"]) is False


def test_core_families_are_real_families() -> None:
    """Guard against a typo silently making a target family match nothing."""
    from streamlit_app.tagging import ALL_JOB_FAMILIES

    assert set(TARGET_FAMILIES) <= set(ALL_JOB_FAMILIES)
    assert set(ADJACENT_FAMILIES) <= set(ALL_JOB_FAMILIES)
