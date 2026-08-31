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
from streamlit_app.theme import page_header, section  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    display_profile_url,
    fold_accents,
    format_change,
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

#: Metric cards in a row share a fixed height (see the Network Stats tab).
METRIC_HEIGHT = 120

configure_page("Job Search")
require_login()

page_header(
    "Job search",
    "Every current connection, ranked by how strongly they could refer you "
    "into the company they work at today.",
)

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
with st.container(border=True):
    search_column, company_column, sort_column = st.columns([3, 3, 2], gap="medium")

    with search_column:
        people_search = st.text_input(
            "Name or position contains",
            placeholder="e.g. nguyen, data engineer",
            value="",
            help="Accent-blind: typing “nguyen” finds “Nguyễn”.",
        )

    with company_column:
        company_search = st.text_input(
            "Company contains", placeholder="e.g. Acme Bank", value=""
        )

    with sort_column:
        sort_choice = st.selectbox(
            "Sort by", [SORT_SCORE, SORT_NAME, SORT_COMPANY], index=0
        )

    tag_column, date_column, untagged_column = st.columns([3, 2, 1], gap="medium")
    with tag_column:
        selected_tags = st.pills(
            "Role tags",
            options=list(ALL_TAGS),
            selection_mode="multi",
            default=[],
            help="A connection can carry several tags; select none for everyone.",
        )
    with date_column:
        # Bounds come from the data, not from today: a range the export cannot
        # satisfy would silently empty the table with no way to tell why.
        connected_dates = pd.to_datetime(connections["connected_on"], errors="coerce")
        earliest = connected_dates.min()
        latest_connected = connected_dates.max()
        date_range = st.date_input(
            "Connected between",
            value=(earliest.date(), latest_connected.date())
            if pd.notna(earliest) and pd.notna(latest_connected)
            else (),
            min_value=earliest.date() if pd.notna(earliest) else None,
            max_value=latest_connected.date() if pd.notna(latest_connected) else None,
            help="When you connected on LinkedIn — not when they last changed job.",
        )
    with untagged_column:
        include_untagged = st.checkbox("Include untagged", value=True)

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

# `st.date_input` with a range returns a 1-tuple while the user is mid-pick,
# between clicking the start date and the end date. Filtering on a half-chosen
# range would make the table jump; wait for both ends.
if isinstance(date_range, tuple) and len(date_range) == 2:
    range_start, range_end = date_range
    connected = pd.to_datetime(filtered["connected_on"], errors="coerce")
    filtered = filtered[
        connected.between(pd.Timestamp(range_start), pd.Timestamp(range_end))
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
strong = int((scored["score"] >= STRONG_SCORE_THRESHOLD).sum()) if len(scored) else 0
summary_columns = st.columns(4, gap="medium")
summary_columns[0].metric(
    "Matching connections",
    f"{len(scored):,}",
    delta=f"of {len(connections):,} total",
    delta_arrow="off",
    icon=":material/filter_alt:",
    border=True,
    height=METRIC_HEIGHT,
)
summary_columns[1].metric(
    f"Scoring {STRONG_SCORE_THRESHOLD}+",
    f"{strong:,}",
    icon=":material/star:",
    border=True,
    height=METRIC_HEIGHT,
    help="Shortlist-worthy: a strong role plus at least one other signal.",
)
summary_columns[2].metric(
    "Companies covered",
    # Distinct `company_key`, not distinct raw text: two spellings of one
    # employer are one employer, and counting strings here would disagree with
    # the "Companies" metric on the Network Stats tab — which is exactly the
    # drift `assert_company_counts_agree` exists to stop.
    f"{scored.loc[scored['company'].notna(), 'company_key'].nunique():,}",
    icon=":material/apartment:",
    border=True,
    height=METRIC_HEIGHT,
)
summary_columns[3].metric(
    "Best score",
    f"{int(scored['score'].max()) if len(scored) else 0}",
    delta=f"max possible {DEFAULT_WEIGHTS.maximum}",
    delta_arrow="off",
    icon=":material/trophy:",
    border=True,
    height=METRIC_HEIGHT,
)

# --- Table -----------------------------------------------------------------
table = pd.DataFrame(
    {
        "Name": scored["full_name"],
        "Company": scored["company"].fillna("—"),
        "Position": scored["position"].fillna("—"),
        "Tags": scored["tags"],
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
        "Name": st.column_config.TextColumn("Name", pinned=True),
        "Tags": st.column_config.ListColumn(
            "Tags", help="Derived from the job title."
        ),
        "Profile": st.column_config.LinkColumn(
            "Profile", display_text="Open ↗", width="small"
        ),
        "Why": st.column_config.TextColumn("Why", width="medium"),
        "Score": st.column_config.ProgressColumn(
            "Referral strength",
            help="How strong a referral this person could give.",
            min_value=0,
            max_value=DEFAULT_WEIGHTS.maximum,
            format="%d",
        ),
    },
    row_height=34,
)

st.download_button(
    "Download this shortlist (CSV)",
    # The on-screen Tags column holds real lists so Streamlit can render them
    # as chips; CSV wants them flattened back to text.
    data=table.assign(Tags=scored["tags"].map(format_tags))
    .to_csv(index=False)
    .encode("utf-8"),
    file_name="connection_lens_shortlist.csv",
    mime="text/csv",
    icon=":material/download:",
    help="Contains personal data — keep it on this machine.",
)

# --- Signals ---------------------------------------------------------------
st.divider()
# Stacked, not side by side: an "old → new" cell is twice the width of a plain
# one, and truncating it cuts off the *new* value — the half that matters.
with st.container(border=True):
    section("Recently changed company or title")
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
                    "Company": [
                        format_change(previous, current)
                        for previous, current in zip(
                            changes["previous_company"],
                            changes["company"],
                            strict=True,
                        )
                    ],
                    "Position": [
                        format_change(previous, current)
                        for previous, current in zip(
                            changes["previous_position"],
                            changes["position"],
                            strict=True,
                        )
                    ],
                    "Since": changes["changed_at"].map(format_timestamp),
                    "Profile": changes["connection_id"].map(display_profile_url),
                }
            ),
            width="stretch",
            hide_index=True,
            row_height=34,
            column_config={
                "Name": st.column_config.TextColumn("Name", pinned=True),
                # Wide on purpose: these hold "old → new", not a single value.
                "Company": st.column_config.TextColumn("Company", width="large"),
                "Position": st.column_config.TextColumn("Position", width="large"),
                "Since": st.column_config.TextColumn("Since", width="small"),
                "Profile": st.column_config.LinkColumn(
                    "Profile", display_text="Open ↗", width="small"
                ),
            },
        )
        st.caption("`old → new` marks the field that moved.")

with st.container(border=True):
    section("No longer in the network")
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
                    "Last known profile": departed["connection_id"].map(
                        display_profile_url
                    ),
                }
            ),
            width="stretch",
            hide_index=True,
            row_height=34,
            column_config={
                "Name": st.column_config.TextColumn("Name", pinned=True),
                "Last known profile": st.column_config.LinkColumn(
                    "Last known profile", display_text="Open ↗", width="small"
                ),
            },
        )
        st.caption("Only the fact and the date are recorded — never a reason.")

with st.expander("How referral strength is scored", icon=":material/calculate:"):
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

**Not scored: how recently they changed job.** Recent moves are shown in their
own panel above instead. The **Why** column shows which signals fired.
        """
    )

render_sidebar_footer()
