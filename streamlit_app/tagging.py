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
ENGINEERING: Final = "engineering"
EARLY_CAREER: Final = "early_career"

ALL_TAGS: Final[tuple[str, ...]] = (
    RECRUITER_TALENT,
    LEADERSHIP,
    EXECUTIVE,
    TARGET_PEER,
    ENGINEERING,
    EARLY_CAREER,
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
    # The largest group the first four tags missed: technical people at the
    # companies worth aiming at, who can refer inside their own team.
    ENGINEERING: (
        "engineer",
        "engineering",
        "developer",
        "development",
        "software",
        "programmer",
        "architect",
        "devops",
        "sre",
        "backend",
        "back-end",
        "frontend",
        "front-end",
        "fullstack",
        "full-stack",
        "qa",
        "tester",
    ),
    # Tagged so they can be filtered *out*: a referral from someone still
    # interning rarely carries weight, however friendly they are.
    EARLY_CAREER: (
        "intern",
        "internship",
        "student",
        "fresher",
        "trainee",
        "graduate",
        "apprentice",
        "junior",
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


# ---------------------------------------------------------------------------
# Job families
# ---------------------------------------------------------------------------
# A second, *different* taxonomy over the same `position` text.
#
# Tags above answer "what can this person do for me?" and are multi-label — a
# Director of Analytics is both `leadership` and `target_peer`. Families answer
# "what job is this?" and are **single-label**: a distribution chart needs each
# connection counted exactly once or the bars stop summing to the network.
#
# Order is the whole mechanism: the first matching family wins, so the list runs
# most-specific first. "Senior Analytics Engineer" has to reach
# `Analytics Engineering` before `Data Analytics` or `Software Engineering` can
# claim it, and "Director Business Intelligence" has to reach
# `Business Intelligence` before `Founder & Executive`.

OTHER_FAMILY: Final = "Other"

#: (family, keywords) in match order. Every keyword is matched on word
#: boundaries, same as the tag lists above.
JOB_FAMILY_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("Analytics Engineering", ("analytics engineer", "analytic engineer", "dbt")),
    (
        "Data Engineering",
        (
            "data engineer", "data engineering", "big data", "etl", "elt",
            "data platform", "data infrastructure", "data architect",
            "data warehouse", "databricks", "data pipeline",
        ),
    ),
    ("Data Science", ("data scientist", "data science", "statistician")),
    (
        "AI / Machine Learning",
        (
            "machine learning", "ml engineer", "ai engineer", "deep learning",
            "nlp", "computer vision", "ai", "ml", "llm", "generative ai",
        ),
    ),
    (
        "Business Intelligence",
        ("business intelligence", "bi", "power bi", "tableau", "looker", "qlik"),
    ),
    ("Business Analysis", ("business analyst", "business analysis", "ba")),
    (
        "Data Analytics",
        (
            "data analyst", "data analytics", "analytics", "analytics manager",
            "insight analyst", "insights", "reporting analyst",
            "reporting specialist",
        ),
    ),
    (
        "Talent & Recruiting",
        (
            "recruiter", "recruiting", "recruitment", "talent", "sourcer", "hr",
            "human resources", "people operations", "people partner",
        ),
    ),
    ("Product", ("product manager", "product owner", "product lead", "product")),
    (
        "Design",
        ("designer", "design", "ux", "ui", "graphic", "creative", "art director"),
    ),
    (
        # Before Software Engineering on purpose: "Network Engineer" and
        # "Security Engineer" both contain "engineer" and would otherwise be
        # swallowed by it.
        "IT & Security",
        (
            "cyber security", "cybersecurity", "information security",
            "infosec", "security analyst", "security engineer",
            "it support", "information technology support", "help desk",
            "helpdesk", "system administrator", "sysadmin", "systems engineer",
            "network engineer", "technical support",
        ),
    ),
    (
        "Software Engineering",
        (
            "software engineer", "software", "developer", "development",
            "engineer", "engineering", "programmer", "devops", "sre",
            "backend", "back-end", "frontend", "front-end", "fullstack",
            "full-stack", "architect", "qa", "tester", "quality assurance",
        ),
    ),
    (
        "Finance & Accounting",
        (
            "finance", "financial", "accountant", "accounting", "audit",
            "auditor", "tax", "banking", "credit", "investment", "treasury",
            "actuary", "actuarial",
        ),
    ),
    (
        "Sales & Marketing",
        (
            "sales", "marketing", "business development", "account manager",
            "account executive", "growth", "seo", "brand", "advertising",
            "customer success", "partnership",
        ),
    ),
    ("Consulting", ("consultant", "consulting", "advisory", "advisor")),
    (
        "Academia & Study",
        (
            "student", "lecturer", "professor", "researcher", "research",
            "phd", "teaching", "teacher", "university", "tutor",
        ),
    ),
    (
        "Operations & Delivery",
        (
            "operations", "operation", "supply chain", "logistics",
            "project manager", "programme manager", "program manager",
            "scrum", "agile", "delivery manager",
        ),
    ),
    (
        "Founder & Executive",
        (
            "founder", "co-founder", "cofounder", "ceo", "cto", "coo", "cfo",
            "cio", "chief", "vp", "vice president", "president", "owner",
            "managing director", "partner",
        ),
    ),
)

ALL_JOB_FAMILIES: Final[tuple[str, ...]] = (
    *(family for family, _ in JOB_FAMILY_RULES),
    OTHER_FAMILY,
)

_COMPILED_FAMILY_RULES: Final[tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]] = (
    tuple(
        (
            family,
            tuple(
                re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
                for keyword in keywords
            ),
        )
        for family, keywords in JOB_FAMILY_RULES
    )
)


def job_family(position: str | None) -> str:
    """Put a job title in exactly one family — the first rule that matches.

    Returns `Other` for a blank title or one no rule claims, so every
    connection lands in exactly one bucket and the families always sum to the
    size of the network.
    """
    if not position or not position.strip():
        return OTHER_FAMILY
    text = position.strip()
    for family, patterns in _COMPILED_FAMILY_RULES:
        if any(pattern.search(text) for pattern in patterns):
            return family
    return OTHER_FAMILY
