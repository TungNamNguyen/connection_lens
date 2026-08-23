"""Warm-intro scoring for the Job Search tab.

Pure functions, no Streamlit and no SQL, so the whole thing is unit-testable
(§11, §12). The weights follow the sketch in §15 — +50 for a target-company
match, +20 for a recent job change, +15 for seniority — and are collected in
one dataclass so tuning them never means touching the logic.

Still an open item (§15): the exact weights are not final. Two refinements are
already implemented and deliberately visible in the breakdown:

* a *partial* company match (token overlap) scores half of an exact match,
  because "Example" and "Example Corporation" are the same employer;
* every score carries the reasons that produced it, so a number never has to
  be taken on faith before a real outreach decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

import pandas as pd

from streamlit_app.tagging import EXECUTIVE, LEADERSHIP, tag_connection

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
_WHITESPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True)
class ScoringWeights:
    """Tunable weights — see §15, not yet finalised."""

    company_exact_match: int = 50
    company_partial_match: int = 25
    recent_change: int = 20
    recent_change_window_days: int = 90
    seniority: int = 15


DEFAULT_WEIGHTS: Final = ScoringWeights()


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
    "Acme Bank (ACMB)" and "techcombank" compare equal.
    """
    if not name:
        return ""
    lowered = _PUNCTUATION.sub(" ", name.strip().lower())
    tokens = [
        token for token in _WHITESPACE.sub(" ", lowered).split() if token not in COMPANY_STOPWORDS
    ]
    return " ".join(tokens)


def company_match(company: str | None, target_company: str | None) -> str:
    """Classify an employer against the target company.

    Returns ``"exact"``, ``"partial"`` or ``"none"``.
    """
    left = normalise_company_name(company)
    right = normalise_company_name(target_company)
    if not left or not right:
        return "none"
    if left == right:
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


def score_connection(
    *,
    company: str | None,
    position: str | None,
    days_since_change: float | None,
    target_company: str | None,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> ScoreBreakdown:
    """Score one connection as a warm-intro route into ``target_company``.

    With no target company the score is 0 by design: the table is then sorted
    by recency of change instead (§9).
    """
    if not target_company or not target_company.strip():
        return ScoreBreakdown()

    components: dict[str, int] = {}
    reasons: list[str] = []

    match = company_match(company, target_company)
    if match == "exact":
        components["company_exact_match"] = weights.company_exact_match
        reasons.append(f"works at {company}")
    elif match == "partial":
        components["company_partial_match"] = weights.company_partial_match
        reasons.append(f"company looks related to target ({company})")

    if (
        days_since_change is not None
        and not pd.isna(days_since_change)
        and days_since_change <= weights.recent_change_window_days
    ):
        components["recent_change"] = weights.recent_change
        reasons.append(f"changed company/title {int(days_since_change)} days ago")

    if has_seniority_signal(position):
        components["seniority"] = weights.seniority
        reasons.append("seniority signal in title")

    return ScoreBreakdown(
        total=sum(components.values()), components=components, reasons=reasons
    )


def score_connections(
    frame: pd.DataFrame,
    target_company: str | None,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
    *,
    company_column: str = "company",
    position_column: str = "position",
    days_column: str = "days_since_change",
) -> pd.DataFrame:
    """Add ``score`` and ``score_reason`` columns to a connections table."""
    scored = frame.copy()
    if scored.empty:
        scored["score"] = pd.Series(dtype="int64")
        scored["score_reason"] = pd.Series(dtype="object")
        return scored

    breakdowns = [
        score_connection(
            company=row.get(company_column),
            position=row.get(position_column),
            days_since_change=row.get(days_column),
            target_company=target_company,
            weights=weights,
        )
        for _, row in scored.iterrows()
    ]
    scored["score"] = [breakdown.total for breakdown in breakdowns]
    scored["score_reason"] = [breakdown.reason_text for breakdown in breakdowns]
    return scored
