"""Companies tab — where your network actually reaches (§9).

The Job Search tab ranks people. This one ranks employers, because that is the
order a job search happens in: pick where you want to work, then find the way
in. It answers two questions the person ranking cannot:

* which employers you have several ways into, for the roles you actually apply
  for — a peer on the team, someone who can sign the headcount, an in-house
  recruiter;
* which employers you have **no front door** to. There, a referral is not a
  shortcut past the queue, it is the whole route.

The reach numbers count people, not scores: a count stays interpretable, and
it never quietly inherits the person ranking's assumptions.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from streamlit_app import charts, db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.companies import (  # noqa: E402
    WEIGHT_CORE,
    WEIGHT_DATA_LEADER,
    WEIGHT_DATA_PERSON,
    WEIGHT_RECRUITER,
    company_reach,
    summarise,
)
from streamlit_app.tagging import (  # noqa: E402
    ADJACENT_FAMILIES,
    ALL_JOB_FAMILIES,
    TARGET_FAMILIES,
)
from streamlit_app.theme import page_header, section  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    fold_accents,
    render_sidebar_footer,
    require_warehouse,
)

#: Metric cards in a row share a fixed height, as on every other tab.
METRIC_HEIGHT = 120

#: How many employers the chart draws before the tail turns into single bars.
CHART_TOP_N = 15

configure_page("Companies")
require_login()

page_header(
    "Companies",
    "Where your network reaches for the roles you are applying for — and "
    "which employers you have no front door to.",
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

# --- What counts as "your field" -------------------------------------------
with st.container(border=True):
    target_column, adjacent_column = st.columns(2, gap="medium")
    with target_column:
        target_families = st.multiselect(
            "Roles you are applying for",
            options=list(ALL_JOB_FAMILIES),
            default=list(TARGET_FAMILIES),
            help=(
                "Reach is measured **relative to these families**. Change them "
                "and the ranking changes with them."
            ),
        )
    with adjacent_column:
        adjacent_families = st.multiselect(
            "Adjacent, can still refer across",
            options=list(ALL_JOB_FAMILIES),
            default=list(ADJACENT_FAMILIES),
            help="Counted as evidence the company employs technical people.",
        )

if not target_families:
    st.warning("Pick at least one target family to rank against.", icon="🎯")
    st.stop()

reach = company_reach(
    connections, target_families=target_families, adjacent_families=adjacent_families
)
totals = summarise(reach)

# --- Headline --------------------------------------------------------------
metric_columns = st.columns(4, gap="medium")
metric_columns[0].metric(
    "Employers reachable",
    f"{totals['companies']:,}",
    delta=f"{totals['with_core']:,} with someone in your field",
    delta_arrow="off",
    icon=":material/apartment:",
    border=True,
    height=METRIC_HEIGHT,
)
metric_columns[1].metric(
    "With a front door",
    f"{totals['front_door']:,}",
    icon=":material/meeting_room:",
    border=True,
    height=METRIC_HEIGHT,
    help="You know an in-house recruiter there.",
)
metric_columns[2].metric(
    "No front door, but a leader",
    f"{totals['closed_with_leader']:,}",
    icon=":material/key:",
    border=True,
    height=METRIC_HEIGHT,
    help=(
        "No recruiter you know, but someone senior enough in a relevant field "
        "to create the role. A referral is the whole route here."
    ),
)
metric_columns[3].metric(
    "All three ways in",
    f"{totals['all_three']:,}",
    icon=":material/verified:",
    border=True,
    height=METRIC_HEIGHT,
    help="A peer in your field, a leader who can hire, and a recruiter.",
)

st.divider()

# --- Filters + table -------------------------------------------------------
section("Ranked employers", "Reach counts the ways in, not the people you know.")

filter_columns = st.columns([3, 2, 2], gap="medium")
with filter_columns[0]:
    company_search = st.text_input(
        "Company contains", placeholder="e.g. bank", value=""
    )
with filter_columns[1]:
    door = st.radio(
        "Front door",
        ["Any", "Has one", "None — referral only"],
        horizontal=False,
    )
with filter_columns[2]:
    only_in_field = st.checkbox(
        "Only where someone works in my field", value=True,
        help="Hides employers you know people at, but nobody doing your job.",
    )

filtered = reach
if company_search.strip():
    needle = fold_accents(company_search.strip())
    filtered = filtered[
        filtered["company"].map(fold_accents).str.contains(needle, regex=False)
    ]
if door == "Has one":
    filtered = filtered[filtered["has_front_door"]]
elif door == "None — referral only":
    filtered = filtered[~filtered["has_front_door"]]
if only_in_field:
    filtered = filtered[filtered["core_peers"] > 0]

if filtered.empty:
    st.info("No employer matches these filters.", icon="🔍")
    render_sidebar_footer()
    st.stop()

table = pd.DataFrame(
    {
        "Company": filtered["company"],
        "Reach": filtered["reach"],
        "In your field": filtered["core_peers"],
        "Data people": filtered["data_people"],
        "Can hire": filtered["data_leaders"],
        "Recruiters": filtered["recruiters"],
        "Front door": filtered["has_front_door"].map({True: "✅", False: "—"}),
        "You know": filtered["connections"],
    }
)

st.dataframe(
    table,
    width="stretch",
    hide_index=True,
    row_height=34,
    column_config={
        "Company": st.column_config.TextColumn("Company", pinned=True, width="medium"),
        "Reach": st.column_config.ProgressColumn(
            "Reach",
            help="core×3 + leaders×3 + recruiters×2 + data people×1",
            min_value=0,
            max_value=int(filtered["reach"].max()),
            format="%d",
        ),
        "Can hire": st.column_config.NumberColumn(
            "Can hire", help="Leader or executive in a relevant field.", width="small"
        ),
        "Front door": st.column_config.TextColumn(
            "Front door", help="An in-house recruiter you know.", width="small"
        ),
    },
)

st.caption(
    f"{len(filtered):,} of {len(reach):,} employers shown. Open the **Job Search** "
    "tab and filter by a company name to see who to approach there."
)

st.download_button(
    "Download this list (CSV)",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name="connection_lens_companies.csv",
    mime="text/csv",
    icon=":material/download:",
    help="Contains personal data — keep it on this machine.",
)

# --- Chart -----------------------------------------------------------------
st.divider()
top = filtered.head(CHART_TOP_N)
section(
    f"Top {len(top)} by reach",
    "The same ranking, so the gap between the leaders and the tail is visible.",
)
st.altair_chart(
    charts.ranked_bar_chart(
        top.rename(columns={"company": "Company", "reach": "Reach"}),
        charts.active_palette(),
        label_column="Company",
        value_column="Reach",
        label_title="Company",
        value_title="Reach",
        height=charts.ranked_bar_height(len(top)),
    ),
    use_container_width=True,
    # As on every other tab: the palette comes from `charts`, not from
    # Streamlit's own chart theme.
    theme=None,
)

with st.expander("How reach is counted", icon=":material/calculate:"):
    st.markdown(
        f"""
| Contact | Weight | Why |
| --- | --- | --- |
| Someone in a family you are applying for | ×{WEIGHT_CORE} | Proof the employer hires this role, and they hear about openings on their own team first |
| Someone senior enough to hire in a relevant field | ×{WEIGHT_DATA_LEADER} | They can create the headcount rather than route you to one |
| An in-house recruiter | ×{WEIGHT_RECRUITER} | The front door — but only to roles that are already open |
| Anyone else in a data or adjacent family | ×{WEIGHT_DATA_PERSON} | Evidence there is a technical team at all |

**No front door** means no in-house recruiter *you know* — not that the company
has none. Where that is true, the people who can create or pre-announce a role
are the whole route in, so start with the ones marked **Can hire**.

Reach counts **people, not scores**. It is deliberately not the sum of the Job
Search referral strengths: that would mix a property of the employer with a
property of the person, and neither number would be readable afterwards.
        """
    )

render_sidebar_footer()
