"""Network Stats tab — read-only charts from the marts layer (§9).

Laid out as a dashboard rather than one long scroll: a headline strip that is
always visible, then four panels of related detail behind tabs. Every number
and chart is the same one the previous layout showed — only the arrangement
and the chrome changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collections import Counter  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from streamlit_app import charts, db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.scoring import (  # noqa: E402
    DEFAULT_WEIGHTS,
    has_seniority_signal,
    score_connections,
)
from streamlit_app.tagging import (  # noqa: E402
    ALL_JOB_FAMILIES,
    ALL_TAGS,
    EARLY_CAREER,
    JOB_FAMILY_RULES,
    TAG_DESCRIPTIONS,
    TAG_KEYWORDS,
    job_family,
    tag_connection,
)
from streamlit_app.theme import page_header, section  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    format_timestamp,
    render_sidebar_footer,
    require_warehouse,
)

#: Score bands for the referral-reach distribution, weakest first.
SCORE_BANDS: list[tuple[str, int, int]] = [
    ("0 — no signal", 0, 0),
    ("1–24", 1, 24),
    ("25–49", 25, 49),
    ("50–74", 50, 74),
    ("75+", 75, 1_000),
]

#: A company is a "stronghold" once this many connections work there.
STRONGHOLD_MINIMUM = 3

#: Every chart in a side-by-side row shares a height, so the two panels line up.
PANEL_CHART_HEIGHT = 260

#: …and so does the panel around it. Left to size themselves, a panel whose
#: heading carries a two-line caption stands taller than its neighbour and the
#: row reads as crooked, so the pair is pinned to one height instead.
#:
#: Sized to the tallest content in a row: a 66px two-line heading, a 260px
#: chart and the container's own padding. A caption-less panel keeps ~40px of
#: slack — the price of equal heights, and cheaper than compensating each
#: chart's height for its heading, which would only line up at one window
#: width.
PANEL_HEIGHT = 372

#: Metric cards in a row share a fixed height. Left to size themselves, a card
#: with a `delta` grows taller than its neighbours and the row reads as ragged.
METRIC_HEIGHT = 128

#: The two distribution charts keep a fixed viewport and scroll inside it, so
#: raising "show top N" makes the chart longer without making the page longer —
#: the panels stay side by side and aligned whatever N each one is set to.
BREAKDOWN_VIEWPORT_HEIGHT = 430

#: Bounds on the "show top N" input. Below 5 the chart says nothing; past 100 a
#: ranked bar chart stops being readable however far you scroll.
TOP_N_MIN, TOP_N_MAX, TOP_N_DEFAULT, TOP_N_STEP = 5, 100, 15, 5

configure_page("Network Stats")
require_login()

page_header(
    "Network stats",
    "Who is in the network, how it has changed between exports, and how much "
    "referral reach it actually carries.",
)

status = db.warehouse_status()
require_warehouse(
    status,
    "marts",
    "Upload an export and run the ingestion DAG — these charts read the "
    "`mart_network_stats` and `mart_network_breakdown` models.",
)

stats = db.load_network_stats()
if stats.empty:
    st.info("The marts are built but hold no snapshots yet.", icon="⏳")
    st.stop()

palette = charts.active_palette()
latest = stats.iloc[-1]
has_history = len(stats) > 1

connections = db.load_current_connections()
monthly = db.load_connected_over_time()
if not monthly.empty:
    monthly = monthly.assign(year_month=pd.to_datetime(monthly["year_month"]))


def chart_panel(title: str, caption: str | None = None, *, height: int | None = None):
    """A bordered card holding one chart, with its own heading.

    `height` pins panels that sit side by side to the same size. Without it a
    panel whose heading carries a two-line caption grows taller than the one
    beside it and the row reads as crooked.
    """
    container = st.container(border=True, height=height or "content")
    with container:
        section(title, caption)
    return container


# --- Headline strip --------------------------------------------------------
headline = st.columns(5, gap="medium")
headline[0].metric(
    "Connections",
    f"{int(latest['total_connections']):,}",
    delta=int(latest["net_change"]) if has_history else None,
    delta_description="vs previous" if has_history else None,
    icon=":material/group:",
    border=True,
    height=METRIC_HEIGHT,
)
headline[1].metric(
    "Companies",
    f"{int(latest['distinct_companies']):,}",
    icon=":material/apartment:",
    border=True,
    height=METRIC_HEIGHT,
)
headline[2].metric(
    "Joined",
    f"{int(latest['new_connections']):,}",
    icon=":material/person_add:",
    border=True,
    height=METRIC_HEIGHT,
)
headline[3].metric(
    "Left",
    f"{int(latest['lost_connections']):,}",
    icon=":material/person_remove:",
    border=True,
    height=METRIC_HEIGHT,
)
headline[4].metric(
    "Email coverage",
    f"{float(latest['email_coverage_pct'] or 0):.1f}%",
    icon=":material/mail:",
    border=True,
    height=METRIC_HEIGHT,
    help="LinkedIn only exports an address when the connection opted in, so "
    "most rows are legitimately blank.",
)
st.caption(
    f"As of snapshot {format_timestamp(latest['snapshot_ts'])} · "
    f"{len(stats)} snapshot(s) in history"
)

growth_tab, composition_tab, reach_tab, quality_tab = st.tabs(
    ["Growth", "Composition", "Referral reach", "Data quality"]
)

# --- Growth ----------------------------------------------------------------
with growth_tab:
    if not has_history:
        st.info(
            "Growth and churn need at least two snapshots. Upload a newer "
            "export later and the trend lines will fill in.",
            icon="📈",
        )
    else:
        growth_column, churn_column = st.columns(2, gap="medium")
        with growth_column, chart_panel(
            "Network size over time", height=PANEL_HEIGHT
        ):
            st.altair_chart(
                charts.growth_chart(stats, palette, height=PANEL_CHART_HEIGHT),
                use_container_width=True,
                theme=None,
            )
        with churn_column, chart_panel(
            "Joined vs left, per snapshot",
            "“Left” means the connection was absent from that export. No "
            "reason is inferred — LinkedIn's export gives none.",
            height=PANEL_HEIGHT,
        ):
            st.altair_chart(
                charts.churn_chart(stats, palette, height=PANEL_CHART_HEIGHT),
                use_container_width=True,
                theme=None,
            )

    build_column, cumulative_column = st.columns(2, gap="medium")
    with build_column, chart_panel(
        "When the network was built",
        "Counted from each connection's own date, so this works from the very "
        "first export — unlike the growth chart, which compares snapshots.",
        height=PANEL_HEIGHT,
    ):
        if monthly.empty:
            st.caption("No connection dates available yet.")
        else:
            st.altair_chart(
                charts.monthly_connections_chart(
                    monthly, palette, height=PANEL_CHART_HEIGHT
                ),
                use_container_width=True,
                theme=None,
            )

    with cumulative_column, chart_panel(
        "Cumulative network size", height=PANEL_HEIGHT
    ):
        if monthly.empty:
            st.caption("No connection dates available yet.")
        else:
            cumulative = monthly.sort_values("year_month").assign(
                cumulative_connections=lambda frame: frame["connection_count"].cumsum()
            )
            st.altair_chart(
                charts.cumulative_connections_chart(
                    cumulative, palette, height=PANEL_CHART_HEIGHT
                ),
                use_container_width=True,
                theme=None,
            )

# --- Composition -----------------------------------------------------------
with composition_tab:
    def breakdown_panel(
        title: str,
        dimension_type: str,
        label_title: str,
        *,
        key: str,
    ) -> None:
        """One distribution chart with its own "show top N" control.

        Company and job title are free text, so they fragment very differently —
        each panel gets its own N rather than sharing one, and each says how many
        distinct values it is ranking so "top 15" is never mistaken for "most of
        the network".
        """
        section(title)
        distinct_values = db.count_breakdown_values(dimension_type)
        undisclosed = db.load_undisclosed_count(dimension_type)
        top_n = int(
            st.number_input(
                "Show top",
                min_value=TOP_N_MIN,
                max_value=TOP_N_MAX,
                value=TOP_N_DEFAULT,
                step=TOP_N_STEP,
                key=key,
                width=140,
                help=f"{distinct_values:,} distinct values in the latest snapshot.",
            )
        )
        frame = db.load_breakdown(dimension_type, top_n=top_n)
        if frame.empty:
            st.caption("No data yet.")
            return

        with st.container(height=BREAKDOWN_VIEWPORT_HEIGHT, border=False):
            st.altair_chart(
                charts.ranked_bar_chart(
                    frame,
                    palette,
                    label_column="dimension_value",
                    value_column="connection_count",
                    label_title=label_title,
                    value_title="Connections",
                    height=charts.ranked_bar_height(len(frame)),
                ),
                use_container_width=True,
                theme=None,
            )
        st.caption(
            f"Showing {len(frame)} of {distinct_values:,} distinct values · "
            f"{undisclosed:,} connection(s) disclosed none and are excluded "
            "from this chart."
        )

    company_column, position_column = st.columns(2, gap="medium")

    with company_column, st.container(border=True):
        breakdown_panel(
            "Top companies",
            "company",
            "Company",
            key="top_companies_n",
        )

    with position_column, st.container(border=True):
        breakdown_panel(
            "Top job titles",
            "position",
            "Job title",
            key="top_positions_n",
        )

    # --- job families ------------------------------------------------------
    with st.container(border=True):
        section(
            "Job families",
            "Job titles are free text, so the Top-job-titles chart above ranks "
            "*strings*, and there are nearly as many of those as there are "
            "people. This groups them into one family each by keyword, which "
            "is what makes “how many data engineers do I know?” answerable.",
        )
        if connections.empty:
            st.caption("No current connections yet.")
        else:
            family_top_n = int(
                st.number_input(
                    "Show top",
                    min_value=TOP_N_MIN,
                    max_value=len(ALL_JOB_FAMILIES),
                    value=min(TOP_N_DEFAULT, len(ALL_JOB_FAMILIES)),
                    step=1,
                    key="top_families_n",
                    width=140,
                    help=f"{len(ALL_JOB_FAMILIES)} families in the taxonomy.",
                )
            )
            family_counts = Counter(connections["position"].map(job_family))
            family_frame = pd.DataFrame(
                [
                    {"family": family, "connections": count}
                    for family, count in family_counts.most_common(family_top_n)
                ]
            )
            with st.container(height=BREAKDOWN_VIEWPORT_HEIGHT, border=False):
                st.altair_chart(
                    charts.ranked_bar_chart(
                        family_frame,
                        palette,
                        label_column="family",
                        value_column="connections",
                        label_title="Job family",
                        value_title="Connections",
                        height=charts.ranked_bar_height(len(family_frame)),
                    ),
                    use_container_width=True,
                    theme=None,
                )
            st.caption(
                f"Showing {len(family_frame)} of {len(ALL_JOB_FAMILIES)} families · "
                "every connection lands in exactly one, so the bars sum to the "
                f"network ({len(connections):,})."
            )

    with chart_panel(
        "Role mix",
        "Tags come from `streamlit_app/tagging.py`, the same function the Job "
        "Search tab uses — the taxonomy is never duplicated in SQL. A "
        "connection can carry several tags.",
    ):
        if connections.empty:
            st.caption("No current connections yet.")
        else:
            tag_counts = {
                tag: int(
                    connections["position"]
                    .map(lambda value, tag=tag: tag in tag_connection(value))
                    .sum()
                )
                for tag in ALL_TAGS
            }
            untagged = int(
                connections["position"].map(lambda value: not tag_connection(value)).sum()
            )
            tag_frame = pd.DataFrame(
                [{"tag": tag, "connections": count} for tag, count in tag_counts.items()]
                + [{"tag": "untagged", "connections": untagged}]
            )
            tag_chart_column, tag_legend_column = st.columns([2, 1], gap="medium")
            with tag_chart_column:
                st.altair_chart(
                    charts.ranked_bar_chart(
                        tag_frame,
                        palette,
                        label_column="tag",
                        value_column="connections",
                        label_title="Tag",
                        value_title="Connections",
                        height=220,
                    ),
                    use_container_width=True,
                    theme=None,
                )
            with tag_legend_column:
                for tag, description in TAG_DESCRIPTIONS.items():
                    st.markdown(f"**`{tag}`** — {description}")

    # --- the rules themselves ----------------------------------------------
    with st.expander(
        "How tagging and job families are decided", icon=":material/rule:"
    ):
        st.markdown(
            """
Both taxonomies read one field — the job title — and match **keywords on word
boundaries**, so `hr` never fires inside "chrome" and `bi` never inside
"biology". Matching is case-insensitive. Neither ever looks at the company,
the name, or anything else.

They differ in one way that matters:

| | Role tags | Job families |
| --- | --- | --- |
| Labels per person | **Several** — a Director of Analytics is both `leadership` and `target_peer` | **Exactly one** — the first rule that matches wins |
| Answers | "what can this person do for me?" | "what job is this?" |
| Used by | the Job Search filter and the referral score | the Job families chart |
| Unmatched | no tags — still listed, just deprioritised | `Other` |

Because families are single-label, **rule order is the whole mechanism**: the
list runs most-specific first so "Senior Analytics Engineer" reaches
`Analytics Engineering` before `Data Analytics` or `Software Engineering` can
claim it, and "Network Engineer" reaches `IT & Security` before
`Software Engineering`.

Both live in `streamlit_app/tagging.py` as pure functions with their own
pytest suite — never duplicated in SQL, which is what would let them drift.
            """
        )

        st.markdown("**Role tag keywords**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Tag": tag, "Matches any of": ", ".join(keywords)}
                    for tag, keywords in TAG_KEYWORDS.items()
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "Tag": st.column_config.TextColumn("Tag", width="small"),
                "Matches any of": st.column_config.TextColumn(
                    "Matches any of", width="large"
                ),
            },
        )
        st.caption(
            "Plus a regex for C-level titles no list can enumerate: "
            "`\\bc[a-z]o\\b` catches CEO, CTO, CFO, CIO, COO."
        )

        st.markdown("**Job family rules, in match order**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "#": index,
                        "Family": family,
                        "Matches any of": ", ".join(keywords),
                    }
                    for index, (family, keywords) in enumerate(JOB_FAMILY_RULES, 1)
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "#": st.column_config.NumberColumn("#", width="small"),
                "Family": st.column_config.TextColumn("Family", width="medium"),
                "Matches any of": st.column_config.TextColumn(
                    "Matches any of", width="large"
                ),
            },
        )
        st.caption(
            "Anything no rule claims becomes `Other` — mostly titles that are "
            "pure seniority with no function in them, like “Director” or "
            "“Team Lead”."
        )

    if not connections.empty:
        section("Career stage and reachability")
        stage_columns = st.columns(4, gap="medium")
        senior_count = int(connections["position"].map(has_seniority_signal).sum())
        early_count = int(
            connections["position"]
            .map(lambda value: EARLY_CAREER in tag_connection(value))
            .sum()
        )
        with_email = int(connections["email_address"].notna().sum())
        no_company = int(connections["company"].isna().sum())
        stage_columns[0].metric(
            "Senior titles",
            f"{senior_count:,}",
            f"{100 * senior_count / len(connections):.0f}%",
            delta_arrow="off",
            border=True,
            height=METRIC_HEIGHT,
        )
        stage_columns[1].metric(
            "Early career",
            f"{early_count:,}",
            f"{100 * early_count / len(connections):.0f}%",
            delta_arrow="off",
            border=True,
            height=METRIC_HEIGHT,
        )
        stage_columns[2].metric(
            "Reachable by email",
            f"{with_email:,}",
            f"{100 * with_email / len(connections):.0f}%",
            delta_arrow="off",
            border=True,
            height=METRIC_HEIGHT,
        )
        stage_columns[3].metric(
            "No employer listed",
            f"{no_company:,}",
            f"{100 * no_company / len(connections):.0f}%",
            delta_arrow="off",
            border=True,
            height=METRIC_HEIGHT,
        )

# --- Referral reach --------------------------------------------------------
with reach_tab:
    section(
        "Referral reach",
        "The same score the Job Search tab ranks by: how strongly each "
        "connection could refer you into the company they work at today.",
    )

    if connections.empty:
        st.caption("No current connections yet.")
    else:
        scored = score_connections(connections)
        reach_columns = st.columns(4, gap="medium")
        reach_columns[0].metric(
            "Median score",
            f"{int(scored['score'].median())}",
            icon=":material/functions:",
            border=True,
            height=METRIC_HEIGHT,
        )
        reach_columns[1].metric(
            "Scoring 75+",
            f"{int((scored['score'] >= 75).sum()):,}",
            icon=":material/star:",
            border=True,
            height=METRIC_HEIGHT,
        )
        reach_columns[2].metric(
            "Scoring 50+",
            f"{int((scored['score'] >= 50).sum()):,}",
            icon=":material/thumb_up:",
            border=True,
            height=METRIC_HEIGHT,
        )
        reach_columns[3].metric(
            "Scoring 0",
            f"{int((scored['score'] == 0).sum()):,}",
            icon=":material/remove:",
            border=True,
            height=METRIC_HEIGHT,
        )

        band_frame = pd.DataFrame(
            [
                {
                    "band": label,
                    "connections": int(scored["score"].between(low, high).sum()),
                }
                for label, low, high in SCORE_BANDS
            ]
        )
        band_column, stronghold_column = st.columns(2, gap="medium")
        with band_column, chart_panel(
            "Referral strength distribution",
            f"Maximum possible score is {DEFAULT_WEIGHTS.maximum}. Scoring 0 "
            "usually means an untagged title and an old connection, not that "
            "the person is useless — only a missing employer rules a referral "
            "out.",
            height=PANEL_HEIGHT,
        ):
            st.altair_chart(
                charts.ordinal_bar_chart(
                    band_frame,
                    palette,
                    label_column="band",
                    value_column="connections",
                    label_title="Referral strength",
                    value_title="Connections",
                    height=220,
                ),
                use_container_width=True,
                theme=None,
            )

        with stronghold_column, chart_panel(
            "Strongholds",
            f"Companies where {STRONGHOLD_MINIMUM}+ of your connections work — "
            "deep enough that one introduction can be cross-checked with "
            "someone else.",
            height=PANEL_HEIGHT,
        ):
            strongholds = (
                scored[scored["company"].notna()]
                .groupby("company")
                .agg(
                    connections=("connection_id", "count"),
                    best_score=("score", "max"),
                    median_score=("score", "median"),
                )
                .query(f"connections >= {STRONGHOLD_MINIMUM}")
                .sort_values(["best_score", "connections"], ascending=False)
                .head(12)
                .reset_index()
            )
            if strongholds.empty:
                st.caption(
                    f"No company has {STRONGHOLD_MINIMUM} or more of your "
                    "connections yet."
                )
            else:
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Company": strongholds["company"],
                            "Connections": strongholds["connections"],
                            "Best score": strongholds["best_score"],
                            "Median": strongholds["median_score"].astype(int),
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Best score": st.column_config.ProgressColumn(
                            "Best score",
                            min_value=0,
                            max_value=DEFAULT_WEIGHTS.maximum,
                            format="%d",
                        ),
                    },
                )

# --- Data quality ----------------------------------------------------------
with quality_tab:
    section(
        "Data quality of the latest snapshot",
        "Restricted profiles export as a date-only row with no profile URL, so "
        "they cannot be given a stable identity and are excluded from the Gold "
        "layer — counted here rather than dropped silently.",
    )
    quality_columns = st.columns(3, gap="medium")
    quality_columns[0].metric(
        "Restricted profile rows",
        int(latest["restricted_profile_rows"]),
        icon=":material/visibility_off:",
        border=True,
        height=METRIC_HEIGHT,
    )
    quality_columns[1].metric(
        "No company disclosed",
        int(latest["connections_without_company"]),
        icon=":material/domain_disabled:",
        border=True,
        height=METRIC_HEIGHT,
        help="Counted over every row in the export, restricted profiles "
        "included — so it is larger than the `(unknown)` bucket on the "
        "Composition tab, which can only count rows that reached the Gold "
        "layer.",
    )
    quality_columns[2].metric(
        "No job title disclosed",
        int(latest["connections_without_position"]),
        icon=":material/work_off:",
        border=True,
        height=METRIC_HEIGHT,
        help="Counted over every row in the export, restricted profiles "
        "included — see the note on the metric to its left.",
    )

    st.markdown("")
    section("Snapshot history")
    st.dataframe(
        stats.assign(snapshot_ts=stats["snapshot_ts"].map(format_timestamp))[
            [
                "snapshot_ts",
                "total_connections",
                "new_connections",
                "lost_connections",
                "net_change",
                "distinct_companies",
                "connections_with_email",
                "restricted_profile_rows",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "snapshot_ts": "Snapshot",
            "total_connections": "Total",
            "new_connections": "Joined",
            "lost_connections": "Left",
            "net_change": "Net",
            "distinct_companies": "Companies",
            "connections_with_email": "With email",
            "restricted_profile_rows": "Restricted",
        },
    )

render_sidebar_footer()
