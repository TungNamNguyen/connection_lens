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
from streamlit_app.scoring import DEFAULT_WEIGHTS, score_connections  # noqa: E402
from streamlit_app.tagging import ALL_TAGS, format_tags, tag_connection  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    display_profile_url,
    fold_accents,
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
    people_search = st.text_input(
        "Name or position contains",
        placeholder="e.g. nguyen, data engineer",
        value="",
        help="Accent-blind: typing “nguyen” finds “Nguyễn”.",
    )

with filter_columns[2]:
    company_search = st.text_input(
        "Company contains", placeholder="e.g. Acme Bank", value=""
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

if people_search.strip():
    # One box over both fields: you either remember the person or the job.
    needle = fold_accents(people_search.strip())
    haystack = filtered["full_name"].map(fold_accents) + " " + filtered[
        "position"
    ].map(fold_accents)
    filtered = filtered[haystack.str.contains(needle, regex=False)]

if company_search.strip():
    needle = fold_accents(company_search.strip())
    filtered = filtered[
        filtered["company"].map(fold_accents).str.contains(needle, regex=False)
    ]

scored = score_connections(filtered)

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
summary_columns[3].metric(
    "Companies covered", f"{scored['company'].dropna().nunique():,}"
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
The score answers one question: **how strongly could this person refer you
into the company they work at today?** Nothing to configure — it reads the
employer already in the export.

| Signal | Points |
| --- | --- |
| Recruiter / talent | +{DEFAULT_WEIGHTS.role_recruiter} |
| Executive | +{DEFAULT_WEIGHTS.role_executive} |
| Leadership | +{DEFAULT_WEIGHTS.role_leadership} |
| Peer in your field | +{DEFAULT_WEIGHTS.role_peer} |
| Engineering | +{DEFAULT_WEIGHTS.role_engineering} |
| A second role tag | +{DEFAULT_WEIGHTS.additional_role_tag} |
| Seniority in the title | +{DEFAULT_WEIGHTS.seniority} |
| Connected within {DEFAULT_WEIGHTS.recent_connection_months} months | +{DEFAULT_WEIGHTS.recent_connection} |
| Email in the export | +{DEFAULT_WEIGHTS.reachable_by_email} |
| Early in their career | −{DEFAULT_WEIGHTS.early_career_penalty} |

Only the strongest role tag scores; a second one adds a little rather than
doubling. Someone with **no employer in the export scores 0** — there is
nowhere for them to refer you. Maximum **{DEFAULT_WEIGHTS.maximum}**.

**Not scored: how recently they changed job.** That needs several ingested
exports before it means anything. Recent moves are shown in their own panel
above instead.

The weights live in one dataclass in `streamlit_app/scoring.py`, and the
**Why** column always shows which of them fired.
        """
    )

render_sidebar_footer()
