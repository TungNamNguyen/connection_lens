"""Role tagging taxonomy for the Job Search tab (§9).

One pure function, `tag_connection`, so the taxonomy is testable on its own and
reusable for filtering even when no target company is set. It is deliberately
kept out of SQL: duplicating these keyword lists in a dbt model would
guarantee the two drift apart.

Tags are **not** mutually exclusive — a "Director of Analytics" is both
`leadership` and `target_peer`.
"""

from __future__ import annotations

import re
from typing import Final

RECRUITER_TALENT: Final = "recruiter_talent"
LEADERSHIP: Final = "leadership"
EXECUTIVE: Final = "executive"
TARGET_PEER: Final = "target_peer"

ALL_TAGS: Final[tuple[str, ...]] = (
    RECRUITER_TALENT,
    LEADERSHIP,
    EXECUTIVE,
    TARGET_PEER,
)

TAG_DESCRIPTIONS: Final[dict[str, str]] = {
    RECRUITER_TALENT: "Direct path to referrals and open roles",
    LEADERSHIP: "Likely to have hiring authority or influence",
    EXECUTIVE: "Highest-leverage warm intro if relevant",
    TARGET_PEER: "Same career track — informational chats and in-field referrals",
}

#: Keyword lists per §9. Every entry is matched on word boundaries, so "hr"
#: does not fire on "chrome" and "bi" does not fire on "biology".
TAG_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    RECRUITER_TALENT: (
        "recruiter",
        "recruiting",
        "recruitment",
        "talent",
        "sourcer",
        "hr",
        "human resources",
        "people",
    ),
    LEADERSHIP: (
        "manager",
        "management",
        "director",
        "head",
        "lead",
        "leader",
        "leadership",
        "principal",
    ),
    EXECUTIVE: (
        "chief",
        "vp",
        "vice president",
        "founder",
        "co-founder",
        "president",
        "cxo",
    ),
    TARGET_PEER: (
        "data",
        "analytics",
        "business intelligence",
        "bi",
        "data science",
        "data scientist",
        "data engineer",
        "data engineering",
    ),
}

#: C-level titles that no keyword list can enumerate: CEO, CTO, CFO, CIO, COO…
_C_LEVEL_PATTERN: Final = re.compile(r"\bc[a-z]o\b", re.IGNORECASE)

_COMPILED_KEYWORDS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    tag: tuple(
        re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE) for keyword in keywords
    )
    for tag, keywords in TAG_KEYWORDS.items()
}


def tag_connection(position: str | None) -> list[str]:
    """Return every role tag matching a job title, in taxonomy order.

    An untagged connection returns an empty list — it is still shown in the
    Job Search table, just deprioritised by the default sort (§9).
    """
    if not position or not position.strip():
        return []
    text = position.strip()
    tags = [
        tag
        for tag in ALL_TAGS
        if any(pattern.search(text) for pattern in _COMPILED_KEYWORDS[tag])
    ]
    if EXECUTIVE not in tags and _C_LEVEL_PATTERN.search(text):
        tags.append(EXECUTIVE)
    return sorted(tags, key=ALL_TAGS.index)


def format_tags(tags: list[str]) -> str:
    """Render tags for a table cell."""
    return ", ".join(tags) if tags else "—"
