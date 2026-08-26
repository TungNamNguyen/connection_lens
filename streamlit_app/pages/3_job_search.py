"""Job Search tab — the warm-intro workspace (§9).

Ranks every current connection by **how strong a referral they could give**,
reading the Gold tables directly and applying tagging and scoring in Python so
filtering stays interactive and the taxonomy/weights live in one testable
place instead of being frozen into SQL.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from streamlit_app import db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.scoring import (  # noqa: E402
    DEFAULT_WEIGHTS,
    company_match,
    score_connections,
)
from streamlit_app.tagging import ALL_TAGS, format_tags, tag_connection  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    display_profile_url,
    format_date,
    format_timestamp,
    render_sidebar_footer,
    require_warehouse,
)

SORT_SCORE = "Referral strength (high → low)"
SORT_NAME = "Name (A → Z)"
SORT_COMPANY = "Company (A → Z)"

#: Above this, a connection is worth putting on a shortlist rather than
#: scrolling past. Half the scale, i.e. a strong role plus one other signal.
STRONG_SCORE_THRESHOLD = 50

configure_page("Job Search")
require_login()
st.title("🎯 Job search")

status = db.warehouse_status()
require_warehouse(
    status,
    "gold",
    "Upload an export and run the ingestion DAG — this tab reads "
    "`dim_connection` and `dim_company`.",
)

connections = db.load_current_connections()
if connections.empty:
    st.info("No current connections in the warehouse yet.", icon="⏳")
    st.stop()

connections = connections.assign(
    tags=connections["position"].map(tag_connection),
    profile_url=connections["connection_id"].map(display_profile_url),
)

# --- Filters ---------------------------------------------------------------
filter_columns = st.columns([2, 2, 2, 2])

with filter_columns[0]:
    selected_tags = st.multiselect(
        "Role tags",
        options=list(ALL_TAGS),
        default=[],
        help="A connection can carry several tags; leave empty for everyone.",
    )
    include_untagged = st.checkbox("Include untagged", value=True)

with filter_columns[1]:
    company_search = st.text_input(
        "Company contains", placeholder="e.g. Acme Bank", value=""
    )

with filter_columns[2]:
    target_company = st.text_input(
        "Target company", placeholder="Where do you want to work?", value=""
    )

with filter_columns[3]:
    sort_choice = st.selectbox(
        "Sort by", [SORT_SCORE, SORT_NAME, SORT_COMPANY], index=0
    )

filtered = connections
if selected_tags:
    def _matches_tags(tags: list[str]) -> bool:
        if tags:
            return bool(set(tags) & set(selected_tags))
        return include_untagged

    filtered = filtered[filtered["tags"].map(_matches_tags)]
elif not include_untagged:
    filtered = filtered[filtered["tags"].map(bool)]

if company_search.strip():
    needle = company_search.strip().lower()
    filtered = filtered[
        filtered["company"].fillna("").str.lower().str.contains(needle, regex=False)
    ]

scored = score_connections(filtered, target_company)

if sort_choice == SORT_SCORE:
    scored = scored.sort_values(
        ["score", "full_name"], ascending=[False, True], na_position="last"
    )
elif sort_choice == SORT_COMPANY:
    scored = scored.sort_values("company", ascending=True, na_position="last")
else:
    scored = scored.sort_values("full_name", ascending=True, na_position="last")

# --- Summary ---------------------------------------------------------------
summary_columns = st.columns(4)
summary_columns[0].metric("Matching connections", f"{len(scored):,}")
summary_columns[1].metric("Of total", f"{len(connections):,}")
strong = int((scored["score"] >= STRONG_SCORE_THRESHOLD).sum()) if len(scored) else 0
summary_columns[2].metric(f"Scoring {STRONG_SCORE_THRESHOLD}+", f"{strong:,}")
if target_company.strip():
    # Count people actually employed there — a high score alone does not mean
    # someone works at the target, now that the score stands on its own.
    at_target = int(
        scored["company"]
        .map(lambda name: company_match(name, target_company) == "exact")
        .sum()
    )
    summary_columns[3].metric(f"At {target_company.strip()}", f"{at_target:,}")
else:
    summary_columns[3].metric(
        "Top score", f"{int(scored['score'].max()) if len(scored) else 0}"
    )

# --- Table -----------------------------------------------------------------
table = pd.DataFrame(
    {
        "Name": scored["full_name"],
        "Company": scored["company"].fillna("—"),
        "Position": scored["position"].fillna("—"),
        "Tags": scored["tags"].map(format_tags),
        "Connected": scored["connected_on"].map(format_date),
        "Score": scored["score"],
        "Why": scored["score_reason"],
        "Profile": scored["profile_url"],
    }
)

st.dataframe(
    table,
    width="stretch",
    hide_index=True,
    column_config={
        "Profile": st.column_config.LinkColumn("Profile", display_text="Open ↗"),
        "Score": st.column_config.ProgressColumn(
            "Referral strength",
            help="How strong a referral this person could give — see the "
            "breakdown below the table.",
            min_value=0,
            max_value=DEFAULT_WEIGHTS.maximum,
            format="%d",
        ),
    },
)

st.caption(
    "**Referral strength** ranks who could realistically get your CV in front "
    "of someone. It scores the person, so it is useful before you have a "
    "target company in mind — naming one adds the largest single term."
)

st.download_button(
    "Download this shortlist (CSV)",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name="connection_lens_shortlist.csv",
    mime="text/csv",
    help="Contains personal data — keep it on this machine.",
)

# --- Signals ---------------------------------------------------------------
st.divider()
signal_columns = st.columns(2)

with signal_columns[0]:
    st.subheader("Recently changed company or title")
    changes = db.load_recent_changes(limit=15)
    if changes.empty:
        st.caption(
            "No changes recorded yet — this fills in once a second export "
            "shows someone in a new role."
        )
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Name": changes["full_name"],
                    "Now at": changes["company"].fillna("—"),
                    "As": changes["position"].fillna("—"),
                    "Since": changes["changed_at"].map(format_timestamp),
                }
            ),
            width="stretch",
            hide_index=True,
        )

with signal_columns[1]:
    st.subheader("No longer in the network")
    departed = db.load_departed_connections(limit=15)
    if departed.empty:
        st.caption("Nobody has dropped out of an export yet.")
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Name": departed["full_name"],
                    "Last seen at": departed["company"].fillna("—"),
                    "Absent since": departed["absent_since"].map(format_timestamp),
                }
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Only the fact and the date are recorded. LinkedIn's export gives "
            "no signal for *why* someone disappeared, so none is guessed."
        )

with st.expander("How referral strength is scored"):
    st.markdown(
        f"""
The score answers one question: **how strong a referral could this person
give?** It scores the person, so it is meaningful before you name a target —
naming one just adds the largest single term.

| Signal | Points | Why it counts |
| --- | --- | --- |
| Works at the target company | +{DEFAULT_WEIGHTS.target_company_exact} | Only someone inside can refer you in |
| Company related to the target | +{DEFAULT_WEIGHTS.target_company_partial} | Same group or a name variant |
| Recruiter / talent | +{DEFAULT_WEIGHTS.role_recruiter} | Moving CVs is their job |
| Executive | +{DEFAULT_WEIGHTS.role_executive} | Senior enough to create a role |
| Leadership | +{DEFAULT_WEIGHTS.role_leadership} | Usually holds hiring authority |
| Peer in your field | +{DEFAULT_WEIGHTS.role_peer} | Can vouch for your work credibly |
| A second role tag | +{DEFAULT_WEIGHTS.additional_role_tag} | "Director of Analytics" beats "Director" |
| Seniority in the title | +{DEFAULT_WEIGHTS.seniority} | A senior voice carries further |
| Connected within {DEFAULT_WEIGHTS.recent_connection_months} months | +{DEFAULT_WEIGHTS.recent_connection} | They are more likely to remember you |
| Email in the export | +{DEFAULT_WEIGHTS.reachable_by_email} | Reachable without InMail |

Only the strongest role tag scores; a second one adds a little rather than
doubling. Maximum **{DEFAULT_WEIGHTS.maximum}**.

**Not scored: how recently they changed job.** That signal needs several
ingested exports before it means anything, and until then it fires for
everyone equally — which ranks nothing. Recent moves are shown as their own
panel above instead.

The weights live in one dataclass in `streamlit_app/scoring.py`, and the
**Why** column always shows which of them fired.
        """
    )

render_sidebar_footer()
