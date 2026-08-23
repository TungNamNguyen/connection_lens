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
from streamlit_app.tagging import ALL_TAGS, TAG_DESCRIPTIONS, tag_connection  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    format_timestamp,
    render_sidebar_footer,
    require_warehouse,
)

configure_page("Network Stats")
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
