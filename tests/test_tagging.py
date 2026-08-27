"""Role tagging taxonomy (§9)."""

from __future__ import annotations

import pytest

from streamlit_app.tagging import (
    ALL_JOB_FAMILIES,
    ALL_TAGS,
    EARLY_CAREER,
    ENGINEERING,
    EXECUTIVE,
    JOB_FAMILY_RULES,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_PEER,
    format_tags,
    job_family,
    tag_connection,
)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Technical Recruiter", [RECRUITER_TALENT]),
        ("Associate Recruitment Consultant", [RECRUITER_TALENT]),
        ("Talent Acquisition Partner", [RECRUITER_TALENT]),
        ("Senior Engineering Manager", [LEADERSHIP, ENGINEERING]),
        ("Engineering Lead", [LEADERSHIP, ENGINEERING]),
        ("Principal Architect", [LEADERSHIP, ENGINEERING]),
        ("Chief Technology Officer", [EXECUTIVE]),
        ("VP of Engineering", [EXECUTIVE, ENGINEERING]),
        ("Co-Founder", [EXECUTIVE]),
        ("Data Scientist", [TARGET_PEER]),
        ("Business Intelligence Analyst", [TARGET_PEER]),
        ("BI Developer", [TARGET_PEER, ENGINEERING]),
        ("Junior Graphic Designer", [EARLY_CAREER]),
        ("AI Product Customer Support", []),
    ],
)
def test_tagging_matches_the_documented_taxonomy(
    position: str, expected: list[str]
) -> None:
    assert tag_connection(position) == expected


def test_tags_are_not_mutually_exclusive() -> None:
    """A Director of Analytics is both leadership and a target peer (§9)."""
    assert tag_connection("Director Business Intelligence") == [LEADERSHIP, TARGET_PEER]
    assert tag_connection("Senior Analytics Engineer, Tech Lead") == [
        LEADERSHIP,
        TARGET_PEER,
        ENGINEERING,
    ]


def test_c_level_titles_are_caught_by_the_regex() -> None:
    for title in ("CTO", "CEO of Acme", "cfo", "Group CIO"):
        assert EXECUTIVE in tag_connection(title), title


@pytest.mark.parametrize(
    "position",
    ["Chrome Colour Consultant", "Biology Researcher", "Threat Analyst", "Cabin Crew"],
)
def test_short_keywords_do_not_match_inside_other_words(position: str) -> None:
    """`hr`, `bi`, `vp` must not fire on chrome / biology / threat."""
    assert tag_connection(position) == []


@pytest.mark.parametrize("position", [None, "", "   "])
def test_missing_position_is_untagged(position: str | None) -> None:
    assert tag_connection(position) == []


def test_tags_come_back_in_taxonomy_order() -> None:
    tags = tag_connection("Head of Data, Recruiting & Strategy")
    assert tags == sorted(tags, key=ALL_TAGS.index)


def test_format_tags_renders_a_dash_when_untagged() -> None:
    assert format_tags([]) == "—"
    assert format_tags([LEADERSHIP, TARGET_PEER]) == "leadership, target_peer"


# --- the two tags added after profiling a real export ----------------------
@pytest.mark.parametrize(
    "position",
    ["Software Engineer", "Fullstack Developer", "DevOps Engineer", "QA Tester"],
)
def test_technical_titles_are_tagged_engineering(position: str) -> None:
    """187 engineers, 117 "software" and 56 developers sat untagged before."""
    assert ENGINEERING in tag_connection(position)


@pytest.mark.parametrize(
    "position",
    ["Data Engineering Intern", "Graduate Trainee", "Junior Developer", "Student"],
)
def test_early_career_titles_are_tagged_so_they_can_be_filtered_out(
    position: str,
) -> None:
    assert EARLY_CAREER in tag_connection(position)


def test_a_sales_executive_is_not_a_c_level_executive() -> None:
    """"Executive" in a Vietnamese title usually means a junior IC."""
    assert EXECUTIVE not in tag_connection("Sales Executive")
    assert EXECUTIVE in tag_connection("Chief Executive Officer")


# --- job families ----------------------------------------------------------
# Families are single-label, so what matters is not just "does it match" but
# "does the *right* rule win" — order is the whole mechanism.
@pytest.mark.parametrize(
    ("position", "expected"),
    [
        # Specificity: each of these contains keywords from a later family too.
        ("Senior Analytics Engineer, Tech Lead", "Analytics Engineering"),
        ("Data Engineer", "Data Engineering"),
        ("Director Business Intelligence", "Business Intelligence"),
        ("Business Intelligence Analyst", "Business Intelligence"),
        ("Data Analyst", "Data Analytics"),
        ("Data & Insights Analyst", "Data Analytics"),
        ("AI Engineer", "AI / Machine Learning"),
        ("Data Scientist", "Data Science"),
        ("Business Analyst", "Business Analysis"),
        # "Network Engineer" contains "engineer"; IT & Security must win.
        ("Network Engineer", "IT & Security"),
        ("Cyber Security Analyst", "IT & Security"),
        ("Senior Engineering Manager", "Software Engineering"),
        ("Technical Recruiter", "Talent & Recruiting"),
        ("Talent Acquisition Specialist", "Talent & Recruiting"),
        ("Chief Technology Officer", "Founder & Executive"),
        ("Co-Founder", "Founder & Executive"),
        ("Fashion Design Intern", "Design"),
        # No functional signal at all — seniority alone is not a family.
        ("Kitchen Team Leader", "Other"),
        ("Director", "Other"),
        (None, "Other"),
        ("", "Other"),
    ],
)
def test_job_family_picks_the_most_specific_rule(position, expected) -> None:
    assert job_family(position) == expected


def test_every_title_lands_in_exactly_one_known_family() -> None:
    """The chart's bars must sum to the network, so nothing may fall through."""
    for position in ("Data Engineer", "Kitchen Team Leader", None, "   ", "???"):
        assert job_family(position) in ALL_JOB_FAMILIES


def test_families_are_ordered_most_specific_first() -> None:
    """A regression guard on the ordering the whole taxonomy depends on."""
    families = [family for family, _ in JOB_FAMILY_RULES]
    assert families.index("Analytics Engineering") < families.index("Data Analytics")
    assert families.index("Business Intelligence") < families.index("Data Analytics")
    assert families.index("IT & Security") < families.index("Software Engineering")
    assert families.index("Data Engineering") < families.index("Software Engineering")
    assert families.index("Talent & Recruiting") < families.index("Founder & Executive")
