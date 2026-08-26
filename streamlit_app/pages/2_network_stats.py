"""Network Stats tab — read-only charts from the marts layer (§9)."""

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
from streamlit_app.scoring import (  # noqa: E402
    DEFAULT_WEIGHTS,
    has_seniority_signal,
    score_connections,
)
from streamlit_app.tagging import (  # noqa: E402
    ALL_TAGS,
    EARLY_CAREER,
    TAG_DESCRIPTIONS,
    tag_connection,
)
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

configure_page("Network Stats")
require_login()
st.title("📊 Network stats")

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

metric_columns = st.columns(5)
metric_columns[0].metric("Connections", f"{int(latest['total_connections']):,}")
metric_columns[1].metric("Companies", f"{int(latest['distinct_companies']):,}")
metric_columns[2].metric("Joined", f"{int(latest['new_connections']):,}")
metric_columns[3].metric("Left", f"{int(latest['lost_connections']):,}")
metric_columns[4].metric("Email coverage", f"{float(latest['email_coverage_pct'] or 0):.1f}%")
st.caption(
    f"As of snapshot {format_timestamp(latest['snapshot_ts'])} · "
    f"{len(stats)} snapshot(s) in history"
)

st.divider()

if len(stats) == 1:
    st.info(
        "Growth and churn need at least two snapshots. Upload a newer export "
        "later and the trend lines will fill in.",
        icon="📈",
    )
else:
    growth_column, churn_column = st.columns(2)
    with growth_column:
        st.subheader("Network size over time")
        st.altair_chart(
            charts.growth_chart(stats, palette), use_container_width=True, theme=None
        )
    with churn_column:
        st.subheader("Joined vs left, per snapshot")
        st.altair_chart(
            charts.churn_chart(stats, palette), use_container_width=True, theme=None
        )
        st.caption(
            "“Left” means the connection was absent from that export. No reason "
            "is inferred — LinkedIn's export gives none."
        )

st.divider()

company_column, position_column = st.columns(2)

with company_column:
    st.subheader("Top companies")
    companies = db.load_breakdown("company", top_n=15)
    if companies.empty:
        st.caption("No company data yet.")
    else:
        st.altair_chart(
            charts.ranked_bar_chart(
                companies,
                palette,
                label_column="dimension_value",
                value_column="connection_count",
                label_title="Company",
                value_title="Connections",
            ),
            use_container_width=True,
            theme=None,
        )

with position_column:
    st.subheader("Top job titles")
    positions = db.load_breakdown("position", top_n=15)
    if positions.empty:
        st.caption("No position data yet.")
    else:
        st.altair_chart(
            charts.ranked_bar_chart(
                positions,
                palette,
                label_column="dimension_value",
                value_column="connection_count",
                label_title="Job title",
                value_title="Connections",
            ),
            use_container_width=True,
            theme=None,
        )

st.divider()

st.subheader("When the network was built")
monthly = db.load_connected_over_time()
if monthly.empty:
    st.caption("No connection dates available yet.")
else:
    monthly = monthly.assign(year_month=pd.to_datetime(monthly["year_month"]))
    st.altair_chart(
        charts.monthly_connections_chart(monthly, palette),
        use_container_width=True,
        theme=None,
    )

st.divider()

st.subheader("Role mix")
st.caption(
    "Tags come from `streamlit_app/tagging.py`, the same function the Job "
    "Search tab uses — the taxonomy is never duplicated in SQL. A connection "
    "can carry several tags."
)
connections = db.load_current_connections()
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
    tag_chart_column, tag_legend_column = st.columns([2, 1])
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

st.divider()

# --- Referral reach --------------------------------------------------------
st.subheader("Referral reach")
st.caption(
    "The same score the Job Search tab ranks by: how strongly each connection "
    "could refer you into the company they work at today."
)

scored = score_connections(connections)
reach_columns = st.columns(4)
reach_columns[0].metric("Median score", f"{int(scored['score'].median())}")
reach_columns[1].metric(
    "Scoring 75+", f"{int((scored['score'] >= 75).sum()):,}"
)
reach_columns[2].metric(
    "Scoring 50+", f"{int((scored['score'] >= 50).sum()):,}"
)
reach_columns[3].metric(
    "Scoring 0", f"{int((scored['score'] == 0).sum()):,}"
)

band_frame = pd.DataFrame(
    [
        {
            "band": label,
            "connections": int(
                scored["score"].between(low, high).sum()
            ),
        }
        for label, low, high in SCORE_BANDS
    ]
)
band_column, stronghold_column = st.columns([1, 1])
with band_column:
    st.altair_chart(
        charts.ranked_bar_chart(
            band_frame,
            palette,
            label_column="band",
            value_column="connections",
            label_title="Referral strength",
            value_title="Connections",
            height=220,
            sort_by_value=False,
        ),
        use_container_width=True,
        theme=None,
    )
    st.caption(
        f"Maximum possible score is {DEFAULT_WEIGHTS.maximum}. Scoring 0 "
        "usually means an untagged title and an old connection, not that the "
        "person is useless — only a missing employer rules a referral out."
    )

with stronghold_column:
    st.markdown(f"**Strongholds** — {STRONGHOLD_MINIMUM}+ connections at one company")
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
            f"No company has {STRONGHOLD_MINIMUM} or more of your connections yet."
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
        )
        st.caption(
            "Where your network is deep enough that one introduction can be "
            "cross-checked with someone else."
        )

st.divider()

# --- Career stage ----------------------------------------------------------
st.subheader("Career stage and reachability")
stage_columns = st.columns(4)
senior_count = int(connections["position"].map(has_seniority_signal).sum())
early_count = int(
    connections["position"].map(lambda value: EARLY_CAREER in tag_connection(value)).sum()
)
with_email = int(connections["email_address"].notna().sum())
no_company = int(connections["company"].isna().sum())
stage_columns[0].metric(
    "Senior titles", f"{senior_count:,}", f"{100 * senior_count / len(connections):.0f}%"
)
stage_columns[1].metric(
    "Early career", f"{early_count:,}", f"{100 * early_count / len(connections):.0f}%"
)
stage_columns[2].metric(
    "Reachable by email", f"{with_email:,}", f"{100 * with_email / len(connections):.0f}%"
)
stage_columns[3].metric(
    "No employer listed", f"{no_company:,}", f"{100 * no_company / len(connections):.0f}%"
)

st.divider()

# --- How the network was built --------------------------------------------
st.subheader("How the network was built")
st.caption(
    "Counted from each connection's own date, so this works from the very "
    "first export — unlike the growth chart above, which compares snapshots."
)
if monthly.empty:
    st.caption("No connection dates available yet.")
else:
    cumulative = monthly.sort_values("year_month").assign(
        cumulative_connections=lambda frame: frame["connection_count"].cumsum()
    )
    st.altair_chart(
        charts.cumulative_connections_chart(cumulative, palette),
        use_container_width=True,
        theme=None,
    )

st.divider()

with st.expander("Data quality of the latest snapshot"):
    quality_columns = st.columns(3)
    quality_columns[0].metric(
        "Restricted profile rows", int(latest["restricted_profile_rows"])
    )
    quality_columns[1].metric(
        "No company disclosed", int(latest["connections_without_company"])
    )
    quality_columns[2].metric(
        "No job title disclosed", int(latest["connections_without_position"])
    )
    st.caption(
        "Restricted profiles export as a date-only row with no profile URL, so "
        "they cannot be given a stable identity and are excluded from the Gold "
        "layer — counted here rather than dropped silently."
    )

with st.expander("Snapshot history"):
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
    )

render_sidebar_footer()
