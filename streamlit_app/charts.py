"""Chart palette and Altair builders for the Network Stats tab.

One place decides how every chart in this app looks, so the tabs read as one
system. Colours come from a validated palette: the blue/red pair passes the
lightness band, chroma floor, colour-vision-deficiency separation, normal-vision
floor and 3:1 contrast checks on both the light and the dark surface.

Rules kept here on purpose:

* one y-axis per chart, never two scales;
* colour follows the entity (Joined is always blue, Left is always red), never
  the current rank;
* single-series charts get one colour and no legend — the title names them;
* hairline solid grid, thin marks, tooltips on every mark.
"""

from __future__ import annotations

from dataclasses import dataclass

import altair as alt
import pandas as pd
import streamlit as st


@dataclass(frozen=True)
class Palette:
    """The colour roles a chart in this app can use."""

    surface: str
    text_primary: str
    text_secondary: str
    grid: str
    series_primary: str
    series_positive: str
    series_negative: str


LIGHT_PALETTE = Palette(
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    grid="#e6e5e1",
    series_primary="#2a78d6",
    series_positive="#2a78d6",
    series_negative="#e34948",
)

DARK_PALETTE = Palette(
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    grid="#33332f",
    series_primary="#3987e5",
    series_positive="#3987e5",
    series_negative="#e66767",
)

JOINED_LABEL = "Joined"
LEFT_LABEL = "Left"


def active_palette() -> Palette:
    """Pick the palette for the viewer's current Streamlit theme.

    The dark palette is a selected set of steps for the dark surface, not an
    automatic inversion of the light one.
    """
    try:
        theme_type = st.context.theme.type  # type: ignore[attr-defined]
    except Exception:
        theme_type = None
    return DARK_PALETTE if theme_type == "dark" else LIGHT_PALETTE


def _styled(chart: alt.Chart, palette: Palette, height: int) -> alt.Chart:
    """Apply the shared chrome: recessive hairline axes, no view border."""
    return (
        chart.properties(height=height)
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor=palette.grid,
            gridWidth=1,
            domainColor=palette.grid,
            tickColor=palette.grid,
            labelColor=palette.text_secondary,
            titleColor=palette.text_secondary,
            labelFontSize=11,
            titleFontSize=11,
        )
        .configure_legend(
            labelColor=palette.text_secondary,
            titleColor=palette.text_secondary,
            labelFontSize=11,
            titleFontSize=11,
            orient="top",
            direction="horizontal",
            title=None,
        )
        .configure_text(color=palette.text_primary)
    )


def growth_chart(stats: pd.DataFrame, palette: Palette, height: int = 260) -> alt.Chart:
    """Total connections per snapshot — one series, so no legend."""
    base = alt.Chart(stats).encode(
        x=alt.X("snapshot_ts:T", title="Snapshot"),
        y=alt.Y(
            "total_connections:Q",
            title="Connections",
            scale=alt.Scale(zero=False, nice=True),
        ),
        tooltip=[
            alt.Tooltip("snapshot_ts:T", title="Snapshot"),
            alt.Tooltip("total_connections:Q", title="Connections", format=","),
            alt.Tooltip("new_connections:Q", title="Joined", format=","),
            alt.Tooltip("lost_connections:Q", title="Left", format=","),
        ],
    )
    line = base.mark_line(strokeWidth=2, color=palette.series_primary)
    points = base.mark_point(
        size=80,
        filled=True,
        color=palette.series_primary,
        stroke=palette.surface,
        strokeWidth=2,
    )
    endpoint = (
        alt.Chart(stats.tail(1))
        .mark_text(dx=-6, dy=-14, fontSize=12, fontWeight="bold", align="right")
        .encode(
            x=alt.X("snapshot_ts:T"),
            y=alt.Y("total_connections:Q"),
            text=alt.Text("total_connections:Q", format=","),
        )
    )
    return _styled(line + points + endpoint, palette, height)


def churn_chart(stats: pd.DataFrame, palette: Palette, height: int = 260) -> alt.Chart:
    """Joined vs left per snapshot on a single signed axis.

    Losses are drawn below zero rather than on a second scale — two y-scales
    would invent a relationship the data does not have.
    """
    joined = stats[["snapshot_ts", "new_connections"]].rename(
        columns={"new_connections": "connections"}
    )
    joined["change_type"] = JOINED_LABEL
    left = stats[["snapshot_ts", "lost_connections"]].rename(
        columns={"lost_connections": "connections"}
    )
    left["connections"] = -left["connections"]
    left["change_type"] = LEFT_LABEL
    long_form = pd.concat([joined, left], ignore_index=True)

    chart = (
        alt.Chart(long_form)
        .mark_bar(cornerRadiusEnd=4, size=18)
        .encode(
            x=alt.X("snapshot_ts:T", title="Snapshot"),
            y=alt.Y("connections:Q", title="Connections gained / lost"),
            color=alt.Color(
                "change_type:N",
                scale=alt.Scale(
                    domain=[JOINED_LABEL, LEFT_LABEL],
                    range=[palette.series_positive, palette.series_negative],
                ),
                legend=alt.Legend(title=None),
            ),
            tooltip=[
                alt.Tooltip("snapshot_ts:T", title="Snapshot"),
                alt.Tooltip("change_type:N", title="Change"),
                alt.Tooltip("connections:Q", title="Connections", format="+,"),
            ],
        )
    )
    return _styled(chart, palette, height)


def ranked_bar_chart(
    frame: pd.DataFrame,
    palette: Palette,
    *,
    label_column: str,
    value_column: str,
    label_title: str,
    value_title: str,
    height: int = 320,
    sort_by_value: bool = True,
) -> alt.Chart:
    """Horizontal bars: one series, one colour, values labelled directly.

    ``sort_by_value=False`` keeps the frame's own order, which is what ordered
    categories (score bands, career stages) need.
    """
    base = alt.Chart(frame).encode(
        y=alt.Y(
            f"{label_column}:N",
            sort="-x" if sort_by_value else None,
            title=label_title,
            axis=alt.Axis(labelLimit=220),
        ),
        x=alt.X(f"{value_column}:Q", title=None, axis=None),
        tooltip=[
            alt.Tooltip(f"{label_column}:N", title=label_title),
            alt.Tooltip(f"{value_column}:Q", title=value_title, format=","),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=14, color=palette.series_primary)
    labels = base.mark_text(align="left", dx=6, fontSize=11).encode(
        text=alt.Text(f"{value_column}:Q", format=",")
    )
    return _styled(bars + labels, palette, height)


def monthly_connections_chart(
    frame: pd.DataFrame, palette: Palette, height: int = 240
) -> alt.Chart:
    """Connections made per calendar month — magnitude over time, one series."""
    base = alt.Chart(frame).encode(
        x=alt.X("year_month:T", title="Month connected"),
        y=alt.Y("connection_count:Q", title="Connections made"),
        tooltip=[
            alt.Tooltip("year_month:T", title="Month", format="%Y-%m"),
            alt.Tooltip("connection_count:Q", title="Connections", format=","),
        ],
    )
    area = base.mark_area(
        color=palette.series_primary, opacity=0.18, line=False
    )
    line = base.mark_line(strokeWidth=2, color=palette.series_primary)
    return _styled(area + line, palette, height)


def cumulative_connections_chart(
    frame: pd.DataFrame, palette: Palette, height: int = 260
) -> alt.Chart:
    """How the network was built up, from connection dates alone.

    Unlike the growth chart this needs only one ingested snapshot: it counts
    when each connection was made, not when an export was taken.
    """
    base = alt.Chart(frame).encode(
        x=alt.X("year_month:T", title="Month connected"),
        y=alt.Y("cumulative_connections:Q", title="Connections in total"),
        tooltip=[
            alt.Tooltip("year_month:T", title="Month", format="%Y-%m"),
            alt.Tooltip("cumulative_connections:Q", title="Total", format=","),
            alt.Tooltip("connection_count:Q", title="Added that month", format=","),
        ],
    )
    area = base.mark_area(color=palette.series_primary, opacity=0.18, line=False)
    line = base.mark_line(strokeWidth=2, color=palette.series_primary)
    return _styled(area + line, palette, height)
