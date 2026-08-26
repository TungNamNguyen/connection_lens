"""Referral strength scoring for the Job Search tab.

The score answers one question: **how strongly could this person refer me into
the company they work at today?** It is a property of the person, so it needs
no target company and no configuration — open the tab and the ranking is
already meaningful.

Pure functions, no Streamlit and no SQL, so the whole thing is unit-testable
(§11, §12). Every weight lives in one dataclass; tuning them never means
touching the logic, and every score carries the reasons that produced it so a
number is never taken on faith before a real outreach decision.

Two things are deliberately **not** scored:

* how recently someone changed company or title — that signal needs several
  ingested exports before it means anything, and until then it fires for
  everyone equally, which ranks nothing;
* a target company typed by hand — a person's referral power belongs to the
  employer already in the export, and filtering by company does the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Final

import pandas as pd

from streamlit_app.tagging import (
    EARLY_CAREER,
    ENGINEERING,
    EXECUTIVE,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_PEER,
    tag_connection,
)

#: Seniority signals not already covered by the leadership/executive tags.
_SENIORITY_PATTERN: Final = re.compile(
    r"\b(senior|sr|staff|expert|principal|lead)\b", re.IGNORECASE
)

MONTHS_PER_YEAR: Final = 12
DAYS_PER_MONTH: Final = 30.44


@dataclass(frozen=True)
class ReferralWeights:
    """How much each signal contributes. The maximum total is 100."""

    # What their role lets them actually do for you, inside their own company.
    # The strongest matching tag counts; a second tag adds on top rather than
    # doubling.
    role_recruiter: int = 40
    role_executive: int = 35
    role_leadership: int = 30
    role_peer: int = 25
    role_engineering: int = 20
    additional_role_tag: int = 10

    # A senior voice carries further inside a hiring process.
    seniority: int = 15

    # The only relationship-warmth signal the export actually contains.
    recent_connection: int = 20
    recent_connection_months: int = MONTHS_PER_YEAR

    # Reachable without depending on LinkedIn InMail.
    reachable_by_email: int = 15

    # Someone still interning rarely carries referral weight, however
    # friendly they are.
    early_career_penalty: int = 20

    @property
    def maximum(self) -> int:
        """The highest score a single connection can reach."""
        return (
            max(
                self.role_recruiter,
                self.role_executive,
                self.role_leadership,
                self.role_peer,
                self.role_engineering,
            )
            + self.additional_role_tag
            + self.seniority
            + self.recent_connection
            + self.reachable_by_email
        )


DEFAULT_WEIGHTS: Final = ReferralWeights()


def role_points(weights: ReferralWeights) -> dict[str, int]:
    """Points per role tag. `early_career` scores nothing — it only subtracts."""
    return {
        RECRUITER_TALENT: weights.role_recruiter,
        EXECUTIVE: weights.role_executive,
        LEADERSHIP: weights.role_leadership,
        TARGET_PEER: weights.role_peer,
        ENGINEERING: weights.role_engineering,
    }


ROLE_REASONS: Final[dict[str, str]] = {
    RECRUITER_TALENT: "recruits for a living",
    EXECUTIVE: "senior enough to create a role",
    LEADERSHIP: "likely has hiring authority",
    TARGET_PEER: "same field — can vouch for your work",
    ENGINEERING: "builds software where you want to work",
}


@dataclass(frozen=True)
class ScoreBreakdown:
    """A score plus the human-readable reasons behind it."""

    total: int = 0
    components: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

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


def months_since(connected_on: object, today: date | None = None) -> float | None:
    """Whole months between a connection date and today, or None if unknown."""
    if connected_on is None or (isinstance(connected_on, float) and pd.isna(connected_on)):
        return None
    try:
        moment = pd.Timestamp(connected_on)
    except (ValueError, TypeError):
        return None
    if pd.isna(moment):
        return None
    reference = pd.Timestamp(today or date.today())
    return (reference - moment).days / DAYS_PER_MONTH


def score_referral(
    *,
    company: str | None,
    position: str | None,
    connected_on: object = None,
    has_email: bool = False,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    today: date | None = None,
) -> ScoreBreakdown:
    """Score how strongly this connection could refer you into their employer."""
    # No employer in the export means there is nowhere for them to refer you.
    if not company or not str(company).strip():
        return ScoreBreakdown(reasons=["no employer in the export"])

    components: dict[str, int] = {}
    reasons: list[str] = []
    tags = tag_connection(position)

    # --- what their role lets them do -------------------------------------
    points = role_points(weights)
    scoring_tags = [tag for tag in tags if tag in points]
    if scoring_tags:
        best = max(scoring_tags, key=lambda tag: points[tag])
        components["role"] = points[best]
        reasons.append(ROLE_REASONS[best])
        others = [tag for tag in scoring_tags if tag != best]
        if others:
            components["second_role"] = weights.additional_role_tag
            reasons.append(f"also tagged {', '.join(others)}")

    if has_seniority_signal(position):
        components["seniority"] = weights.seniority
        reasons.append("seniority signal in title")

    # --- how warm the relationship plausibly is ----------------------------
    months = months_since(connected_on, today)
    if months is not None and months <= weights.recent_connection_months:
        components["recent_connection"] = weights.recent_connection
        reasons.append(f"connected {int(months)} month(s) ago")

    if has_email:
        components["email"] = weights.reachable_by_email
        reasons.append("reachable by email")

    # --- and what works against them ---------------------------------------
    if EARLY_CAREER in tags:
        components["early_career"] = -weights.early_career_penalty
        reasons.append("early in their career")

    return ScoreBreakdown(
        total=max(0, sum(components.values())),
        components=components,
        reasons=reasons,
    )


def score_connections(
    frame: pd.DataFrame,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    *,
    today: date | None = None,
    company_column: str = "company",
    position_column: str = "position",
    connected_on_column: str = "connected_on",
    email_column: str = "email_address",
) -> pd.DataFrame:
    """Add ``score`` and ``score_reason`` columns to a connections table."""
    scored = frame.copy()
    if scored.empty:
        scored["score"] = pd.Series(dtype="int64")
        scored["score_reason"] = pd.Series(dtype="object")
        return scored

    breakdowns = [
        score_referral(
            company=row.get(company_column),
            position=row.get(position_column),
            connected_on=row.get(connected_on_column),
            has_email=bool(pd.notna(row.get(email_column)) and row.get(email_column)),
            weights=weights,
            today=today,
        )
        for _, row in scored.iterrows()
    ]
    scored["score"] = [breakdown.total for breakdown in breakdowns]
    scored["score_reason"] = [breakdown.reason_text for breakdown in breakdowns]
    return scored
