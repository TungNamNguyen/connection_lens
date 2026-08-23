"""Role tagging taxonomy (§9)."""

from __future__ import annotations

import pytest

from streamlit_app.tagging import (
    ALL_TAGS,
    EXECUTIVE,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_PEER,
    format_tags,
    tag_connection,
)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("Technical Recruiter", [RECRUITER_TALENT]),
        ("Associate Recruitment Consultant", [RECRUITER_TALENT]),
        ("Talent Acquisition Partner", [RECRUITER_TALENT]),
        ("Senior Engineering Manager", [LEADERSHIP]),
        ("Engineering Lead", [LEADERSHIP]),
        ("Principal Architect", [LEADERSHIP]),
        ("Chief Technology Officer", [EXECUTIVE]),
        ("VP of Engineering", [EXECUTIVE]),
        ("Co-Founder", [EXECUTIVE]),
        ("Data Scientist", [TARGET_PEER]),
        ("Business Intelligence Analyst", [TARGET_PEER]),
        ("BI Developer", [TARGET_PEER]),
        ("Junior Graphic Designer", []),
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
    ]


def test_c_level_titles_are_caught_by_the_regex() -> None:
    for title in ("CTO", "CEO of Acme", "cfo", "Group CIO"):
        assert EXECUTIVE in tag_connection(title), title


@pytest.mark.parametrize(
    "position",
    ["Chrome Extension Developer", "Biology Researcher", "Threat Analyst", "Cabin Crew"],
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
