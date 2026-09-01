"""Company-level referral reach for the Job Search workflow (§9).

The Job Search tab ranks *people*. This module ranks *companies*, because that
is the order a job search actually happens in: you pick where you want to
work, then you look for a way in.

Pure functions over an already-loaded frame — no Streamlit, no SQL — so the
whole thing is unit-testable, and the taxonomy stays in `tagging.py` instead
of being duplicated into a dbt model where the two would drift apart.

Two things a company table can say that a person table cannot:

* **how many ways in you have** — a peer on the team, someone who can create
  the headcount, and an in-house recruiter are three different doors;
* **whether you have a front door at all** — no in-house recruiter in your
  network means a referral is not a shortcut, it is the only route.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import pandas as pd

from streamlit_app.db import UNKNOWN_LABEL
from streamlit_app.tagging import (
    ADJACENT_FAMILIES,
    EXECUTIVE,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_FAMILIES,
    TARGET_PEER,
    job_family,
    tag_connection,
)

#: The families the owner applies for, and their neighbours, both live with
#: the taxonomy in `tagging.py` — the referral score is measured against the
#: same two lists, and two copies would drift.
__all__ = [
    "ADJACENT_FAMILIES",
    "TARGET_FAMILIES",
    "classify",
    "company_reach",
    "summarise",
]

#: How much each kind of contact opens a door, per §Job search scoring.
#: A peer in the exact family is the strongest single signal that the company
#: hires this role at all; someone who can sign a headcount is worth as much;
#: a recruiter routes you to reqs that are already open, which is useful but
#: only for roles that exist yet.
WEIGHT_CORE: Final = 3
WEIGHT_DATA_LEADER: Final = 3
WEIGHT_RECRUITER: Final = 2
WEIGHT_DATA_PERSON: Final = 1

REACH_COLUMNS: Final[tuple[str, ...]] = (
    "company",
    "reach",
    "core_peers",
    "data_people",
    "data_leaders",
    "recruiters",
    "has_front_door",
    "connections",
)


def _empty_reach() -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype="object") for name in REACH_COLUMNS})


def classify(
    frame: pd.DataFrame,
    *,
    target_families: Iterable[str] = TARGET_FAMILIES,
    adjacent_families: Iterable[str] = ADJACENT_FAMILIES,
    position_column: str = "position",
) -> pd.DataFrame:
    """Tag every row with the facts the company table aggregates.

    Kept separate from :func:`company_reach` so a page can reuse the same
    classification for a drill-down without paying for it twice.
    """
    target = set(target_families)
    adjacent = set(adjacent_families)

    classified = frame.copy()
    if classified.empty:
        for column in ("family", "is_core", "is_data", "is_recruiter", "is_data_leader"):
            classified[column] = pd.Series(dtype="object")
        return classified

    positions = classified[position_column]
    classified["family"] = positions.map(job_family)
    tags = positions.map(tag_connection)

    classified["is_core"] = classified["family"].isin(target)
    classified["is_data"] = classified["family"].isin(target | adjacent)
    classified["is_recruiter"] = tags.map(lambda values: RECRUITER_TALENT in values)
    leads = tags.map(lambda values: bool({LEADERSHIP, EXECUTIVE} & set(values)))

    # A leader only counts if they lead somewhere that could open one of these
    # roles: an Engineering Manager can create a data headcount, a Sales
    # Director cannot.
    #
    # Family alone is too strict here. `job_family` is single-label and its
    # rules are written around what someone *does*, so the broadest titles —
    # "Head of Data", "Data Manager", "Data Lead" — match no family rule and
    # fall into `Other`, while "Chief Data Officer" lands in
    # `Founder & Executive`. Those are precisely the people who can sign a
    # headcount, so the `target_peer` tag (which does fire on them) is accepted
    # as well. The looser tag is used *only* for this flag: for counting peers
    # and technical headcount the precise family is what is wanted, or "Data
    # Entry Supervisor" would start looking like a data team.
    in_relevant_field = classified["is_data"] | tags.map(
        lambda values: TARGET_PEER in values
    )
    classified["is_data_leader"] = leads & in_relevant_field
    return classified


def company_reach(
    frame: pd.DataFrame,
    *,
    target_families: Iterable[str] = TARGET_FAMILIES,
    adjacent_families: Iterable[str] = ADJACENT_FAMILIES,
    company_column: str = "company_name",
) -> pd.DataFrame:
    """One row per employer, ranked by how many ways into it you have.

    Connections with no employer in the export are excluded: they cannot be a
    way into anywhere. They are counted on the Network Stats tab instead, so
    nothing disappears silently.
    """
    if frame.empty or company_column not in frame.columns:
        return _empty_reach()

    classified = classify(
        frame, target_families=target_families, adjacent_families=adjacent_families
    )
    named = classified[
        classified[company_column].notna()
        & (classified[company_column].astype(str).str.strip() != "")
        & (classified[company_column] != UNKNOWN_LABEL)
    ]
    if named.empty:
        return _empty_reach()

    grouped = (
        named.groupby(company_column, dropna=True)
        .agg(
            connections=(company_column, "size"),
            core_peers=("is_core", "sum"),
            data_people=("is_data", "sum"),
            data_leaders=("is_data_leader", "sum"),
            recruiters=("is_recruiter", "sum"),
        )
        .reset_index()
        .rename(columns={company_column: "company"})
    )

    for column in ("connections", "core_peers", "data_people", "data_leaders", "recruiters"):
        grouped[column] = grouped[column].astype(int)

    grouped["reach"] = (
        grouped["core_peers"] * WEIGHT_CORE
        + grouped["data_leaders"] * WEIGHT_DATA_LEADER
        + grouped["recruiters"] * WEIGHT_RECRUITER
        + grouped["data_people"] * WEIGHT_DATA_PERSON
    )
    # No in-house recruiter you know means there is no front door to knock on —
    # which makes a referral the route rather than a shortcut. It says nothing
    # about whether the company employs recruiters, only whether you know one.
    grouped["has_front_door"] = grouped["recruiters"] > 0

    return grouped.sort_values(
        ["reach", "company"], ascending=[False, True]
    ).reset_index(drop=True)[list(REACH_COLUMNS)]


def summarise(reach: pd.DataFrame) -> dict[str, int]:
    """Headline counts for the metric row above the table."""
    if reach.empty:
        return {"companies": 0, "with_core": 0, "front_door": 0, "closed_with_leader": 0,
                "all_three": 0}
    with_core = reach[reach["core_peers"] > 0]
    return {
        "companies": len(reach),
        "with_core": len(with_core),
        "front_door": int(reach["has_front_door"].sum()),
        "closed_with_leader": int(
            (~reach["has_front_door"] & (reach["data_leaders"] > 0)).sum()
        ),
        "all_three": int(
            (
                (reach["core_peers"] > 0)
                & (reach["data_leaders"] > 0)
                & reach["has_front_door"]
            ).sum()
        ),
    }
