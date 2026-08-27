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
    #: Status hues, deliberately kept clear of the categorical series colours
    #: so "healthy" can never be mistaken for "series 3".
    status_ok: str
    status_warn: str
    #: One-hue ordinal ramp, weakest → strongest. Used where a category is
    #: *ordered* (score bands), never where it is merely nominal.
    ordinal_ramp: tuple[str, ...]


LIGHT_PALETTE = Palette(
    # White page, neutral greys. The blue/red series pair re-validates against
    # a #ffffff surface: lightness band, chroma floor, CVD separation and the
    # 3:1 contrast check all still pass.
    surface="#ffffff",
    text_primary="#0b0b0b",
    text_secondary="#52525b",
    grid="#e4e4e7",
    series_primary="#2a78d6",
    series_positive="#2a78d6",
    series_negative="#e34948",
    status_ok="#1baf7a",
    status_warn="#eda100",
    # Starts at step 250, the lightest blue that still clears 2:1 against the
    # light surface — anything lighter dissolves into the page.
    ordinal_ramp=("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"),
)

DARK_PALETTE = Palette(
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    grid="#33332f",
    series_primary="#3987e5",
    series_positive="#3987e5",
    series_negative="#e66767",
    status_ok="#199e70",
    status_warn="#c98500",
    # Mirrored for the dark surface: the *darkest* end is the one that has to
    # clear the surface here, so the ramp stops at step 600.
    ordinal_ramp=("#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"),
)

JOINED_LABEL = "Joined"
LEFT_LABEL = "Left"

#: How a snapshot timestamp is labelled on an axis. Two exports taken on the
#: same day are common, so the time is part of the identity, not decoration.
SNAPSHOT_LABEL_FORMAT = "%d %b %H:%M"


def _with_snapshot_labels(stats: pd.DataFrame) -> pd.DataFrame:
    """Add the ordinal axis label used by the per-snapshot charts.

    Snapshots are **events**, not samples of a continuous signal: they land
    whenever an export is uploaded. Plotting them on a temporal axis spaces
    them by wall-clock distance, which squashes a burst of exports into one
    unreadable column and leaves months of empty axis beside it. An ordinal
    axis gives every snapshot equal width, which is what the eye should be
    comparing.
    """
    labels = pd.to_datetime(stats["snapshot_ts"]).dt.strftime(SNAPSHOT_LABEL_FORMAT)
    return stats.assign(snapshot_label=labels)


#: Connection counts are whole people — never let Vega invent 10.4 of one.
def _count_axis(title: str | None) -> alt.Axis:
    return alt.Axis(title=title, format="d", tickMinStep=1)


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


#: The same stack `.streamlit/config.toml` gives the rest of the app. Vega does
#: not inherit the page font, so it has to be named again here or every chart
#: silently falls back to Vega's own default and the page reads as two designs.
CHART_FONT = (
    "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, "
    "'Noto Sans', sans-serif"
)


def _styled(chart: alt.Chart, palette: Palette, height: int) -> alt.Chart:
    """Apply the shared chrome: recessive hairline axes, no view border."""
    return (
        chart.properties(height=height)
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor=palette.grid,
            gridWidth=1,
            gridDash=[],
            domainColor=palette.grid,
            tickColor=palette.grid,
            tickSize=4,
            labelColor=palette.text_secondary,
            titleColor=palette.text_secondary,
            labelFont=CHART_FONT,
            titleFont=CHART_FONT,
            labelFontSize=11,
            titleFontSize=11,
            titleFontWeight=500,
            labelPadding=4,
            titlePadding=8,
        )
        .configure_legend(
            labelColor=palette.text_secondary,
            titleColor=palette.text_secondary,
            labelFont=CHART_FONT,
            titleFont=CHART_FONT,
            labelFontSize=11,
            titleFontSize=11,
            orient="top",
            direction="horizontal",
            symbolType="circle",
            symbolSize=90,
            offset=6,
            title=None,
        )
        .configure_text(color=palette.text_primary, font=CHART_FONT)
    )


def growth_chart(stats: pd.DataFrame, palette: Palette, height: int = 260) -> alt.Chart:
    """Total connections per snapshot — one series, so no legend."""
    stats = _with_snapshot_labels(stats)
    order = list(stats["snapshot_label"])
    base = alt.Chart(stats).encode(
        x=alt.X(
            "snapshot_label:O",
            sort=order,
            title="Snapshot",
            axis=alt.Axis(labelAngle=0, labelOverlap="greedy"),
        ),
        y=alt.Y(
            "total_connections:Q",
            title="Connections",
            scale=alt.Scale(zero=False, nice=True),
            axis=_count_axis("Connections"),
        ),
        tooltip=[
            alt.Tooltip("snapshot_label:O", title="Snapshot"),
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
            x=alt.X("snapshot_label:O", sort=order),
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
    stats = _with_snapshot_labels(stats)
    order = list(stats["snapshot_label"])
    joined = stats[["snapshot_label", "new_connections"]].rename(
        columns={"new_connections": "connections"}
    )
    joined["change_type"] = JOINED_LABEL
    left = stats[["snapshot_label", "lost_connections"]].rename(
        columns={"lost_connections": "connections"}
    )
    left["connections"] = -left["connections"]
    left["change_type"] = LEFT_LABEL
    long_form = pd.concat([joined, left], ignore_index=True)

    # With two snapshots an uncapped ordinal band makes each bar a slab half
    # the panel wide; with twenty it makes them hairlines. Cap both ends.
    bar_size = max(12, min(32, round(260 / max(len(order), 1))))
    chart = (
        alt.Chart(long_form)
        .mark_bar(cornerRadiusEnd=4, size=bar_size)
        .encode(
            x=alt.X(
                "snapshot_label:O",
                sort=order,
                title="Snapshot",
                axis=alt.Axis(labelAngle=0, labelOverlap="greedy"),
                scale=alt.Scale(paddingInner=0.45),
            ),
            y=alt.Y(
                "connections:Q",
                title="Connections gained / lost",
                axis=_count_axis("Connections gained / lost"),
            ),
            color=alt.Color(
                "change_type:N",
                scale=alt.Scale(
                    domain=[JOINED_LABEL, LEFT_LABEL],
                    range=[palette.series_positive, palette.series_negative],
                ),
                legend=alt.Legend(title=None),
            ),
            tooltip=[
                alt.Tooltip("snapshot_label:O", title="Snapshot"),
                alt.Tooltip("change_type:N", title="Change"),
                alt.Tooltip("connections:Q", title="Connections", format="+,"),
            ],
        )
    )
    return _styled(chart, palette, height)


#: Vertical room one horizontal bar needs. Fixing the *row* height rather than
#: the chart height is what lets a top-50 chart stay as legible as a top-10 one
#: instead of squeezing fifty bars into the same 320px.
BAR_ROW_HEIGHT = 24


def ranked_bar_height(row_count: int) -> int:
    """Chart height for `row_count` horizontal bars, plus room for the axis."""
    return max(180, row_count * BAR_ROW_HEIGHT + 40)


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
            # No axis title: the panel heading already names the dimension, and
            # Vega centres the title on the chart's full height — which drifts
            # out of view entirely once the chart is tall enough to scroll.
            # `label_title` still names the field in the tooltip.
            title=None,
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


def ordinal_bar_chart(
    frame: pd.DataFrame,
    palette: Palette,
    *,
    label_column: str,
    value_column: str,
    label_title: str,
    value_title: str,
    height: int = 240,
) -> alt.Chart:
    """Horizontal bars for an **ordered** category, shaded weakest → strongest.

    Unlike `ranked_bar_chart` this keeps the frame's order and adds a one-hue
    ordinal ramp, so "75+" reads as further along a scale than "1–24" rather
    than as a different thing. The ramp is redundant with the axis labels on
    purpose — colour never carries identity here, it only reinforces order.
    """
    order = list(frame[label_column])
    ramp = list(palette.ordinal_ramp)
    # Fewer bands than ramp steps: take an evenly spread subset so the ends of
    # the scale always land on the ends of the ramp.
    if len(order) < len(ramp):
        step = (len(ramp) - 1) / max(len(order) - 1, 1)
        ramp = [ramp[round(index * step)] for index in range(len(order))]

    base = alt.Chart(frame).encode(
        y=alt.Y(
            f"{label_column}:N",
            sort=order,
            title=label_title,
            axis=alt.Axis(labelLimit=220),
        ),
        x=alt.X(f"{value_column}:Q", title=None, axis=None),
        tooltip=[
            alt.Tooltip(f"{label_column}:N", title=label_title),
            alt.Tooltip(f"{value_column}:Q", title=value_title, format=","),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=16).encode(
        color=alt.Color(
            f"{label_column}:N",
            scale=alt.Scale(domain=order, range=ramp[: len(order)]),
            legend=None,
        )
    )
    labels = base.mark_text(align="left", dx=6, fontSize=11).encode(
        text=alt.Text(f"{value_column}:Q", format=",")
    )
    return _styled(bars + labels, palette, height)


def monthly_connections_chart(
    frame: pd.DataFrame, palette: Palette, height: int = 240
) -> alt.Chart:
    """Connections made per calendar month — one bar per month, one series.

    Bars rather than an area: this is a count of discrete events per bucket,
    which the eye should compare month against month. An area implies a
    continuous quantity flowing between the months, and degenerates into a
    solid block whenever the counts happen to be flat.
    """
    chart = alt.Chart(frame).mark_bar(
        cornerRadiusEnd=3, color=palette.series_primary
    ).encode(
        x=alt.X(
            "year_month:T",
            title="Month connected",
            axis=alt.Axis(format="%b %Y", labelAngle=0, labelOverlap="greedy"),
        ),
        y=alt.Y(
            "connection_count:Q",
            title="Connections made",
            axis=_count_axis("Connections made"),
        ),
        tooltip=[
            alt.Tooltip("year_month:T", title="Month", format="%Y-%m"),
            alt.Tooltip("connection_count:Q", title="Connections", format=","),
        ],
    )
    return _styled(chart, palette, height)


def cumulative_connections_chart(
    frame: pd.DataFrame, palette: Palette, height: int = 260
) -> alt.Chart:
    """How the network was built up, from connection dates alone.

    Unlike the growth chart this needs only one ingested snapshot: it counts
    when each connection was made, not when an export was taken.
    """
    base = alt.Chart(frame).encode(
        x=alt.X("year_month:T", title="Month connected"),
        y=alt.Y(
            "cumulative_connections:Q",
            title="Connections in total",
            axis=_count_axis("Connections in total"),
        ),
        tooltip=[
            alt.Tooltip("year_month:T", title="Month", format="%Y-%m"),
            alt.Tooltip("cumulative_connections:Q", title="Total", format=","),
            alt.Tooltip("connection_count:Q", title="Added that month", format=","),
        ],
    )
    area = base.mark_area(color=palette.series_primary, opacity=0.18, line=False)
    line = base.mark_line(strokeWidth=2, color=palette.series_primary)
    return _styled(area + line, palette, height)
