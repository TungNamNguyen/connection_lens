"""Referral strength scoring for the Job Search tab.

The score answers one question: **how strongly could this person refer you
into a role you are actually applying for, at the company they work at
today?** It is a property of the person, so it needs no target company and no
configuration — open the tab and the ranking is already meaningful.

Pure functions, no Streamlit and no SQL, so the whole thing is unit-testable
(§11, §12). Every weight lives in one dataclass; tuning them never means
touching the logic, and every score carries the reasons that produced it so a
number is never taken on faith before a real outreach decision.

Three rules shape everything below:

* **Relevance comes before power.** A Head of Data can open one of these
  roles; a Sales Director, however senior, cannot. The role weights are a
  matrix of *what someone does* × *how close that is to the roles being
  applied for*, not a seniority ladder.
* **No path means no score.** Someone whose title gives no route in scores
  zero, however recently you connected. Warmth modifies a referral; it cannot
  invent one.
* **Company signal belongs to the company table.** Reach — how many ways into
  an employer you have — is `streamlit_app/companies.py`. The only company
  fact used here is binary and about the *person*: whether a recruiter is
  in-house somewhere that employs data people, or is recruiting for something
  else entirely.

Two things are deliberately **not** scored:

* a target company typed by hand — a person's referral power belongs to the
  employer already in the export, and filtering by company does the rest;
* whether the export lists an email — that is the connection's privacy
  setting, not a measure of how willing they are to refer you. It is a way to
  reach someone, and belongs in a filter.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Final

import pandas as pd

from streamlit_app.tagging import (
    ADJACENT_FAMILIES,
    EARLY_CAREER,
    EXECUTIVE,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_FAMILIES,
    TARGET_PEER,
    job_family,
    tag_connection,
)

#: Seniority signals not already implied by a leadership or executive title.
_SENIORITY_PATTERN: Final = re.compile(
    r"\b(senior|sr|staff|expert|principal|lead)\b", re.IGNORECASE
)

MONTHS_PER_YEAR: Final = 12
DAYS_PER_MONTH: Final = 30.44

# --- referral paths --------------------------------------------------------
# One person has exactly one path in: the first of these that fits. They are
# ordered by how directly that path leads to a role you would apply for.
PATH_FIELD_LEADER: Final = "field_leader"
PATH_FIELD_PEER: Final = "field_peer"
PATH_INHOUSE_RECRUITER: Final = "inhouse_recruiter"
PATH_ADJACENT_LEADER: Final = "adjacent_leader"
PATH_ADJACENT_PEER: Final = "adjacent_peer"
PATH_OUTSIDE_RECRUITER: Final = "outside_recruiter"
PATH_OUTSIDE_LEADER: Final = "outside_leader"
PATH_NONE: Final = "none"
PATH_NO_EMPLOYER: Final = "no_employer"

#: How each path reads in the UI's **Why** column.
PATH_REASONS: Final[dict[str, str]] = {
    PATH_FIELD_LEADER: "can hire in your field",
    PATH_FIELD_PEER: "peer in your field",
    PATH_INHOUSE_RECRUITER: "in-house recruiter where there is a data team",
    PATH_ADJACENT_LEADER: "leads an adjacent team",
    PATH_ADJACENT_PEER: "adjacent field",
    PATH_OUTSIDE_RECRUITER: "recruiter, but no data team there",
    PATH_OUTSIDE_LEADER: "leads outside your field",
    PATH_NONE: "no referral path",
    PATH_NO_EMPLOYER: "no employer in the export",
}


@dataclass(frozen=True)
class ReferralWeights:
    """How much each signal contributes.

    The base points are mutually exclusive — one path, one base — so the scale
    is set by the strongest path plus the modifiers below it.
    """

    # --- base: what this person can do for the roles you apply for ---------
    #: Signs the headcount. Nobody else can create a role that does not exist.
    field_leader: int = 50
    #: On the team: hears about an opening before it is posted, and their
    #: referral is weighed by the people making the decision.
    field_peer: int = 40
    #: Knows every open req, and their employer demonstrably hires this kind
    #: of work.
    inhouse_recruiter: int = 35
    #: Can hire, but for a neighbouring team.
    adjacent_leader: int = 30
    #: Can refer across a team boundary.
    adjacent_peer: int = 18
    #: Recruits, but nowhere that employs the people you would work with —
    #: most often an agency.
    outside_recruiter: int = 15
    #: Senior, but not anywhere that could open one of these roles.
    outside_leader: int = 8

    # --- modifiers ---------------------------------------------------------
    #: A senior voice carries further. Not added to leaders: their base
    #: already prices that in, and adding both counted the same word twice.
    seniority: int = 5
    #: The only relationship-warmth signal the export contains.
    warmth: int = 15
    #: Decay constant in months for that warmth: `warmth · e^(−months/τ)`.
    #: τ = 24 puts the half-life at ≈16.6 months. Continuous on purpose —
    #: a cliff edge at a round number ranks nobody, and a single continuous
    #: term is what stops the whole scale collapsing into a handful of ties.
    warmth_decay_months: float = 24.0
    #: Someone who just moved is onboarding, often carries a referral bonus,
    #: and their employer has just proved it hires. Needs snapshots spaced
    #: over time to mean anything — see `score_referral`.
    recent_move: int = 10
    recent_move_months: int = 6
    #: Someone still interning rarely carries referral weight, however
    #: friendly they are. Kept small: a junior *in your field* is still worth
    #: reaching.
    early_career_penalty: int = 10

    @property
    def base_points(self) -> dict[str, int]:
        """Base points per referral path."""
        return {
            PATH_FIELD_LEADER: self.field_leader,
            PATH_FIELD_PEER: self.field_peer,
            PATH_INHOUSE_RECRUITER: self.inhouse_recruiter,
            PATH_ADJACENT_LEADER: self.adjacent_leader,
            PATH_ADJACENT_PEER: self.adjacent_peer,
            PATH_OUTSIDE_RECRUITER: self.outside_recruiter,
            PATH_OUTSIDE_LEADER: self.outside_leader,
            PATH_NONE: 0,
            PATH_NO_EMPLOYER: 0,
        }

    @property
    def maximum(self) -> int:
        """The highest score a single connection can reach.

        A leader in your field, met recently, who has just moved. Computed
        rather than written down so retuning a weight cannot leave the
        progress bars lying about their scale.
        """
        return max(self.base_points.values()) + self.warmth + self.recent_move


DEFAULT_WEIGHTS: Final = ReferralWeights()

#: How each score component reads in the UI, in the order they are reported.
#: Keys match :attr:`ScoreBreakdown.components`, so a weight that is never
#: earned still shows up — with a count of zero, which is the useful part.
SIGNAL_LABELS: Final[dict[str, str]] = {
    "role": "A way in at their employer",
    "seniority": "Seniority in the title",
    "warmth": "Connected recently",
    "recent_move": "Changed job recently",
    "early_career": "Early career (penalty)",
}


@dataclass(frozen=True)
class ScoreBreakdown:
    """A score plus the human-readable reasons behind it."""

    total: int = 0
    components: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    path: str = PATH_NONE

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "—"


def has_seniority_signal(position: str | None) -> bool:
    """Whether a job title suggests hiring authority or senior standing."""
    if not position:
        return False
    if set(tag_connection(position)) & {LEADERSHIP, EXECUTIVE}:
        return True
    return bool(_SENIORITY_PATTERN.search(position))


def months_since(moment: object, today: date | None = None) -> float | None:
    """Whole months between a date and today, or None if unknown."""
    if moment is None or (isinstance(moment, float) and pd.isna(moment)):
        return None
    try:
        stamp = pd.Timestamp(moment)
    except (ValueError, TypeError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(None) if stamp.tz is not None else stamp.tz_localize(None)
    reference = pd.Timestamp(today or date.today())
    return (reference - stamp).days / DAYS_PER_MONTH


def referral_path(
    position: str | None,
    *,
    family: str | None = None,
    company_has_data_team: bool = False,
    target_families: tuple[str, ...] = TARGET_FAMILIES,
    adjacent_families: tuple[str, ...] = ADJACENT_FAMILIES,
) -> str:
    """Return the single way this person could get you in.

    Order is the whole mechanism: the most direct route that fits wins, so a
    Head of Data is a leader who can hire rather than merely "someone in
    data", and a recruiter is read differently depending on whether their
    employer has anyone doing the work you do.
    """
    family = family or job_family(position)
    tags = set(tag_connection(position))
    leads = bool({LEADERSHIP, EXECUTIVE} & tags)
    in_target = family in target_families
    in_adjacent = family in adjacent_families

    # `job_family` is single-label and written around what someone *does*, so
    # the broadest data titles — "Head of Data", "Data Manager", "Data Lead" —
    # match no family rule at all, and "Chief Data Officer" lands under
    # Founder & Executive. Those are exactly the people who can sign a
    # headcount, so the `target_peer` tag is accepted here as well. It is used
    # only for this branch: as a peer test it is far too loose.
    if leads and (in_target or TARGET_PEER in tags):
        return PATH_FIELD_LEADER
    if in_target:
        return PATH_FIELD_PEER
    if RECRUITER_TALENT in tags:
        return (
            PATH_INHOUSE_RECRUITER if company_has_data_team else PATH_OUTSIDE_RECRUITER
        )
    if leads and in_adjacent:
        return PATH_ADJACENT_LEADER
    if in_adjacent:
        return PATH_ADJACENT_PEER
    if leads:
        return PATH_OUTSIDE_LEADER
    return PATH_NONE


def score_referral(
    *,
    company: str | None,
    position: str | None,
    family: str | None = None,
    company_has_data_team: bool = False,
    connected_on: object = None,
    changed_at: object = None,
    has_previous_version: bool = False,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    today: date | None = None,
    target_families: tuple[str, ...] = TARGET_FAMILIES,
    adjacent_families: tuple[str, ...] = ADJACENT_FAMILIES,
) -> ScoreBreakdown:
    """Score how strongly this connection could refer you into their employer.

    `changed_at` is the SCD2 `dbt_valid_from` of their current row — when this
    version of them first appeared — and `has_previous_version` says whether an
    older, closed version of them exists. Both are required before claiming
    someone changed job: after a first ingestion every row is new, so the
    timestamp alone would announce a job change for the entire network.
    """
    # No employer in the export means there is nowhere for them to refer you.
    if not company or not str(company).strip():
        return ScoreBreakdown(
            reasons=[PATH_REASONS[PATH_NO_EMPLOYER]], path=PATH_NO_EMPLOYER
        )

    path = referral_path(
        position,
        family=family,
        company_has_data_team=company_has_data_team,
        target_families=target_families,
        adjacent_families=adjacent_families,
    )
    base = weights.base_points[path]
    if base <= 0:
        # No route in. Warmth modifies a referral; it cannot invent one, so
        # this stays at zero however recently you connected.
        return ScoreBreakdown(reasons=[PATH_REASONS[path]], path=path)

    components: dict[str, int] = {"role": base}
    reasons: list[str] = [PATH_REASONS[path]]

    if path not in {PATH_FIELD_LEADER, PATH_ADJACENT_LEADER, PATH_OUTSIDE_LEADER} and (
        has_seniority_signal(position)
    ):
        components["seniority"] = weights.seniority
        reasons.append("seniority signal in title")

    months = months_since(connected_on, today)
    if months is not None and months >= 0:
        warmth = round(weights.warmth * math.exp(-months / weights.warmth_decay_months))
        if warmth:
            components["warmth"] = warmth
            reasons.append(f"connected {int(months)} month(s) ago")

    moved = months_since(changed_at, today) if has_previous_version else None
    if moved is not None and 0 <= moved <= weights.recent_move_months:
        components["recent_move"] = weights.recent_move
        reasons.append("changed job recently")

    if EARLY_CAREER in set(tag_connection(position)):
        components["early_career"] = -weights.early_career_penalty
        reasons.append("early in their career")

    return ScoreBreakdown(
        total=max(0, sum(components.values())),
        components=components,
        reasons=reasons,
        path=path,
    )


def company_data_teams(
    frame: pd.DataFrame,
    *,
    company_column: str = "company_key",
    position_column: str = "position",
    target_families: tuple[str, ...] = TARGET_FAMILIES,
    adjacent_families: tuple[str, ...] = ADJACENT_FAMILIES,
) -> set[object]:
    """Employers where you already know someone doing technical data work.

    This is what separates an in-house recruiter from an agency one. Compute
    it on the **whole** network, not on a filtered view: whether a company has
    a data team does not depend on what the reader has typed into a filter.
    """
    if frame.empty or company_column not in frame.columns:
        return set()
    relevant = set(target_families) | set(adjacent_families)
    families = frame[position_column].map(job_family)
    return set(frame.loc[families.isin(relevant), company_column].dropna())


def _breakdowns(
    frame: pd.DataFrame,
    weights: ReferralWeights,
    today: date | None,
    company_column: str,
    position_column: str,
    connected_on_column: str,
    changed_at_column: str,
    previous_version_column: str,
    data_team_column: str,
    target_families: tuple[str, ...],
    adjacent_families: tuple[str, ...],
) -> list[ScoreBreakdown]:
    """Score every row of a connections table, keeping the full breakdowns."""
    return [
        score_referral(
            company=row.get(company_column),
            position=row.get(position_column),
            family=row.get("family"),
            company_has_data_team=bool(row.get(data_team_column, False)),
            connected_on=row.get(connected_on_column),
            changed_at=row.get(changed_at_column),
            has_previous_version=bool(row.get(previous_version_column, False)),
            weights=weights,
            today=today,
            target_families=target_families,
            adjacent_families=adjacent_families,
        )
        for _, row in frame.iterrows()
    ]


def _prepared(
    frame: pd.DataFrame,
    data_team_column: str,
    company_key_column: str,
    position_column: str,
    target_families: tuple[str, ...],
    adjacent_families: tuple[str, ...],
) -> pd.DataFrame:
    """Attach the in-house/agency flag when the caller has not."""
    if data_team_column in frame.columns:
        return frame
    prepared = frame.copy()
    keys = company_data_teams(
        prepared,
        company_column=company_key_column,
        position_column=position_column,
        target_families=target_families,
        adjacent_families=adjacent_families,
    )
    prepared[data_team_column] = (
        prepared[company_key_column].isin(keys)
        if company_key_column in prepared.columns
        else False
    )
    return prepared


def score_connections(
    frame: pd.DataFrame,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    *,
    today: date | None = None,
    company_column: str = "company",
    company_key_column: str = "company_key",
    position_column: str = "position",
    connected_on_column: str = "connected_on",
    changed_at_column: str = "dbt_valid_from",
    previous_version_column: str = "has_previous_version",
    data_team_column: str = "company_has_data_team",
    target_families: tuple[str, ...] = TARGET_FAMILIES,
    adjacent_families: tuple[str, ...] = ADJACENT_FAMILIES,
) -> pd.DataFrame:
    """Add ``score``, ``score_reason`` and ``referral_path`` to a table."""
    scored = frame.copy()
    if scored.empty:
        scored["score"] = pd.Series(dtype="int64")
        scored["score_reason"] = pd.Series(dtype="object")
        scored["referral_path"] = pd.Series(dtype="object")
        return scored

    scored = _prepared(
        scored,
        data_team_column,
        company_key_column,
        position_column,
        target_families,
        adjacent_families,
    )
    breakdowns = _breakdowns(
        scored,
        weights,
        today,
        company_column,
        position_column,
        connected_on_column,
        changed_at_column,
        previous_version_column,
        data_team_column,
        target_families,
        adjacent_families,
    )
    scored["score"] = [breakdown.total for breakdown in breakdowns]
    scored["score_reason"] = [breakdown.reason_text for breakdown in breakdowns]
    scored["referral_path"] = [breakdown.path for breakdown in breakdowns]
    return scored


def signal_frequency(
    frame: pd.DataFrame,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    *,
    today: date | None = None,
    **columns: object,
) -> pd.DataFrame:
    """How many connections each scoring signal fires for.

    Answers "what is actually driving this ranking?". A weight that fires for
    almost everyone separates nobody, and one that fires for almost no one is
    not earning its place — both are invisible in the scores themselves, and
    the weights are still an open question (§15).

    Returns one row per signal in :data:`SIGNAL_LABELS`, zero counts included.
    """
    empty = pd.DataFrame(
        {
            "signal": pd.Series(dtype="object"),
            "connections": pd.Series(dtype="int64"),
        }
    )
    if frame.empty:
        return empty

    scored = score_connections(frame, weights, today=today, **columns)  # type: ignore[arg-type]
    counts: Counter[str] = Counter()
    for reason, path in zip(scored["score_reason"], scored["referral_path"], strict=True):
        # Keys only: this counts *how many connections* a signal fired for,
        # never the points it awarded them.
        if path in {PATH_NONE, PATH_NO_EMPLOYER}:
            continue
        counts["role"] += 1
        if "seniority signal" in reason:
            counts["seniority"] += 1
        if "connected " in reason:
            counts["warmth"] += 1
        if "changed job recently" in reason:
            counts["recent_move"] += 1
        if "early in their career" in reason:
            counts["early_career"] += 1
    return pd.DataFrame(
        [
            {"signal": label, "connections": counts.get(key, 0)}
            for key, label in SIGNAL_LABELS.items()
        ]
    )


def path_frequency(scored: pd.DataFrame) -> pd.DataFrame:
    """How many connections reach you through each kind of route.

    The companion to :func:`signal_frequency`: that one says which weights
    fire, this one says what the network is actually made of.
    """
    empty = pd.DataFrame(
        {"path": pd.Series(dtype="object"), "connections": pd.Series(dtype="int64")}
    )
    if scored.empty or "referral_path" not in scored.columns:
        return empty
    counts = Counter(scored["referral_path"])
    return pd.DataFrame(
        [
            {"path": reason, "connections": counts.get(key, 0)}
            for key, reason in PATH_REASONS.items()
        ]
    )
