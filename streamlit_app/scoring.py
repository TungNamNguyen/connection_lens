"""Referral strength scoring for the Job Search tab.

The score answers one question: **how strong a referral could this person
give me?** It is a property of the person, so it is always available — a
senior recruiter is a good door into the market whether or not a target
company has been named. Naming one adds the single largest term, because
only someone already inside a company can refer you into it.

Pure functions, no Streamlit and no SQL, so the whole thing is unit-testable
(§11, §12). Every weight lives in one dataclass; tuning them never means
touching the logic, and every score carries the reasons that produced it so a
number is never taken on faith before a real outreach decision.

Deliberately **not** part of the score: how recently someone changed company
or title. That signal only exists once several exports have been ingested, and
until then it fires for everyone equally, which is worse than not scoring it
at all. Recent moves are still shown as their own panel on the tab.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Final

import pandas as pd

from streamlit_app.tagging import (
    EXECUTIVE,
    LEADERSHIP,
    RECRUITER_TALENT,
    TARGET_PEER,
    tag_connection,
)

#: Legal-form noise that should not stop two spellings from matching.
COMPANY_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "ltd",
        "limited",
        "llc",
        "plc",
        "jsc",
        "gmbh",
        "pte",
        "group",
        "holdings",
        "vietnam",
        "vn",
        "tnhh",
        "ctcp",
        "cp",
        # Vietnamese legal forms: "Công ty TNHH ..." == "... Company Ltd".
        "công",
        "cong",
        "ty",
    }
)

#: Seniority signals not already covered by the leadership/executive tags.
_SENIORITY_PATTERN: Final = re.compile(
    r"\b(senior|sr|staff|expert|specialist iii)\b", re.IGNORECASE
)

#: Keeps unicode letters (company names carry Vietnamese diacritics).
_PUNCTUATION: Final = re.compile(r"[^\w\s]+", re.UNICODE)
#: "Brand (Legal entity)" — the two halves are separate aliases.
_PARENTHESES: Final = re.compile(r"\([^)]*\)")
_PARENTHESES_CONTENT: Final = re.compile(r"\(([^)]*)\)")
_WHITESPACE: Final = re.compile(r"\s+")

MONTHS_PER_YEAR: Final = 12
DAYS_PER_MONTH: Final = 30.44


@dataclass(frozen=True)
class ReferralWeights:
    """How much each signal contributes. Maximum total is 100."""

    # Only someone inside a company can refer you into it.
    target_company_exact: int = 45
    target_company_partial: int = 22

    # What their role lets them actually do for you. The strongest matching
    # tag counts; a second tag adds a little on top rather than doubling.
    role_recruiter: int = 25
    role_executive: int = 22
    role_leadership: int = 18
    role_peer: int = 15
    additional_role_tag: int = 5

    # A senior voice carries further inside a hiring process.
    seniority: int = 10

    # The only relationship-warmth signal the export actually contains.
    recent_connection: int = 10
    recent_connection_months: int = MONTHS_PER_YEAR

    # Reachable without depending on LinkedIn InMail.
    reachable_by_email: int = 5

    @property
    def maximum(self) -> int:
        """The highest score a single connection can reach."""
        return (
            self.target_company_exact
            + max(
                self.role_recruiter,
                self.role_executive,
                self.role_leadership,
                self.role_peer,
            )
            + self.additional_role_tag
            + self.seniority
            + self.recent_connection
            + self.reachable_by_email
        )


DEFAULT_WEIGHTS: Final = ReferralWeights()


def role_points(weights: ReferralWeights) -> dict[str, int]:
    """Points per role tag, strongest first."""
    return {
        RECRUITER_TALENT: weights.role_recruiter,
        EXECUTIVE: weights.role_executive,
        LEADERSHIP: weights.role_leadership,
        TARGET_PEER: weights.role_peer,
    }


ROLE_REASONS: Final[dict[str, str]] = {
    RECRUITER_TALENT: "recruits for a living",
    EXECUTIVE: "senior enough to create a role",
    LEADERSHIP: "likely has hiring authority",
    TARGET_PEER: "same field — can vouch for your work",
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


def normalise_company_name(name: str | None) -> str:
    """Normalise an employer name for comparison.

    Lower-cases, strips punctuation and drops legal-form words, so
    "Techcombank (TCB)" and "techcombank" compare equal.
    """
    if not name:
        return ""
    lowered = _PUNCTUATION.sub(" ", name.strip().lower())
    tokens = [
        token
        for token in _WHITESPACE.sub(" ", lowered).split()
        if token not in COMPANY_STOPWORDS
    ]
    return " ".join(tokens)


def company_aliases(name: str | None) -> set[str]:
    """Every spelling of a company worth comparing on.

    LinkedIn routinely writes an employer as ``Brand (Legal entity)`` or
    ``Brand (ABBR)`` — "MoMo (M_Service)", "Techcombank (TCB)". Someone
    searching for the brand means the company, so the bracketed part is
    treated as a separate alias rather than as part of one long name.
    """
    if not name:
        return set()
    aliases = set()
    for candidate in (
        name,
        _PARENTHESES.sub(" ", name),
        *_PARENTHESES_CONTENT.findall(name),
    ):
        normalised = normalise_company_name(candidate)
        if normalised:
            aliases.add(normalised)
    return aliases


def company_match(company: str | None, target_company: str | None) -> str:
    """Classify an employer against the target company.

    Returns ``"exact"``, ``"partial"`` or ``"none"``.
    """
    left = normalise_company_name(company)
    right = normalise_company_name(target_company)
    if not left or not right:
        return "none"
    # Any alias matching outright is an exact match: "MoMo (M_Service)" is
    # MoMo, however the export chose to spell it.
    if company_aliases(company) & company_aliases(target_company):
        return "exact"
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return "none"
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return "partial"
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return "partial" if overlap >= 0.5 else "none"


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
    target_company: str | None = None,
    connected_on: object = None,
    has_email: bool = False,
    weights: ReferralWeights = DEFAULT_WEIGHTS,
    today: date | None = None,
) -> ScoreBreakdown:
    """Score how strong a referral this connection could give."""
    components: dict[str, int] = {}
    reasons: list[str] = []

    # --- inside the target company ----------------------------------------
    if target_company and target_company.strip():
        match = company_match(company, target_company)
        if match == "exact":
            components["target_company"] = weights.target_company_exact
            reasons.append(f"works at {company}")
        elif match == "partial":
            components["target_company_partial"] = weights.target_company_partial
            reasons.append(f"company looks related to target ({company})")

    # --- what their role lets them do -------------------------------------
    tags = tag_connection(position)
    if tags:
        points = role_points(weights)
        best = max(tags, key=lambda tag: points[tag])
        components["role"] = points[best]
        reasons.append(ROLE_REASONS[best])
        if len(tags) > 1:
            components["second_role"] = weights.additional_role_tag
            reasons.append(f"also tagged {', '.join(t for t in tags if t != best)}")

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

    return ScoreBreakdown(
        total=sum(components.values()), components=components, reasons=reasons
    )


def score_connections(
    frame: pd.DataFrame,
    target_company: str | None = None,
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
            target_company=target_company,
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
