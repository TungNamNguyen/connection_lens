"""Job Search tab — the warm-intro workspace (§9).

Reads the Gold tables directly and applies tagging and scoring in Python, so
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
    format_timestamp,
    render_sidebar_footer,
    require_warehouse,
)

RECENCY_OPTIONS = {
    "Any": None,
    "Last 30 days": 30,
    "Last 90 days": 90,
    "Last 180 days": 180,
}

SORT_SCORE = "Score (high → low)"
SORT_RECENCY = "Days since change (recent first)"
SORT_NAME = "Name (A → Z)"

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
    recency_label = st.selectbox("Changed within", list(RECENCY_OPTIONS), index=0)
    default_sort = SORT_SCORE if target_company.strip() else SORT_RECENCY
    sort_choice = st.selectbox(
        "Sort by",
        [SORT_SCORE, SORT_RECENCY, SORT_NAME],
        index=[SORT_SCORE, SORT_RECENCY, SORT_NAME].index(default_sort),
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

recency_days = RECENCY_OPTIONS[recency_label]
if recency_days is not None:
    filtered = filtered[filtered["days_since_change"] <= recency_days]

scored = score_connections(filtered, target_company)

if sort_choice == SORT_SCORE:
    scored = scored.sort_values(
        ["score", "days_since_change"], ascending=[False, True]
    )
elif sort_choice == SORT_RECENCY:
    scored = scored.sort_values("days_since_change", ascending=True)
else:
    scored = scored.sort_values("full_name", ascending=True, na_position="last")

# --- Summary ---------------------------------------------------------------
summary_columns = st.columns(4)
summary_columns[0].metric("Matching connections", f"{len(scored):,}")
summary_columns[1].metric("Of total", f"{len(connections):,}")
if target_company.strip():
    at_target = int((scored["score"] >= DEFAULT_WEIGHTS.company_exact_match).sum())
    summary_columns[2].metric(f"At {target_company.strip()}", f"{at_target:,}")
    summary_columns[3].metric(
        "Top score", f"{int(scored['score'].max()) if len(scored) else 0}"
    )
else:
    summary_columns[2].metric(
        "Changed in 90 days",
        f"{int((scored['days_since_change'] <= 90).sum()):,}",
    )
    summary_columns[3].caption(
        "Enter a **target company** to score these connections as warm-intro routes."
    )

# --- Table -----------------------------------------------------------------
table = pd.DataFrame(
    {
        "Name": scored["full_name"],
        "Company": scored["company"].fillna("—"),
        "Position": scored["position"].fillna("—"),
        "Tags": scored["tags"].map(format_tags),
        "Last changed": scored["last_changed_at"].map(format_timestamp),
        "Days since change": scored["days_since_change"],
        "Score": scored["score"],
        "Why": scored["score_reason"],
        "Profile": scored["profile_url"],
    }
)
if not target_company.strip():
    table = table.drop(columns=["Score", "Why"])

st.dataframe(
    table,
    width="stretch",
    hide_index=True,
    column_config={
        "Profile": st.column_config.LinkColumn("Profile", display_text="Open ↗"),
        "Days since change": st.column_config.NumberColumn(
            "Days since change", format="%d"
        ),
        "Score": st.column_config.ProgressColumn(
            "Score",
            min_value=0,
            max_value=int(
                DEFAULT_WEIGHTS.company_exact_match
                + DEFAULT_WEIGHTS.recent_change
                + DEFAULT_WEIGHTS.seniority
            ),
            format="%d",
        ),
    },
)

st.caption(
    "**Last changed** is when this version of the connection first appeared in "
    "the SCD2 history — i.e. when an export first showed the new company or "
    "title, not necessarily when the person actually moved."
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

with st.expander("How the score works"):
    st.markdown(
        f"""
| Component | Weight | When it applies |
| --- | --- | --- |
| Company match (exact) | +{DEFAULT_WEIGHTS.company_exact_match} | Employer matches the target after normalising legal forms and punctuation |
| Company match (partial) | +{DEFAULT_WEIGHTS.company_partial_match} | Names overlap — "Example" vs "Example Corporation" |
| Recent change | +{DEFAULT_WEIGHTS.recent_change} | Company/title changed within {DEFAULT_WEIGHTS.recent_change_window_days} days |
| Seniority | +{DEFAULT_WEIGHTS.seniority} | Leadership/executive tag, or a senior/staff title |

The weights are a starting point, not a finished formula — they live in one
dataclass in `streamlit_app/scoring.py` and every score carries the reasons
that produced it in the **Why** column.
        """
    )

render_sidebar_footer()
