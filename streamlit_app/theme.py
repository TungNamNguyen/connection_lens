"""The app's visual system: one stylesheet and the layout primitives that use it.

Most of the look lives in `.streamlit/config.toml` as design tokens — colours,
radii, fonts, heading scale — because tokens survive Streamlit upgrades in a way
that CSS aimed at internal class names does not. What is left here is the small
set of things tokens cannot express: the page header, the panel/section rhythm,
status pills and tag pills.

Two rules kept on purpose:

* **colours are never written twice.** The stylesheet is generated in Python from
  the same `charts.Palette` the Altair builders use, so a blue in the chrome is
  by construction the blue in the charts;
* **the CSS is decoration only.** Every selector here may stop matching after a
  Streamlit upgrade and the app still reads correctly — nothing structural, no
  layout that only works with the stylesheet applied.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from typing import Literal

import streamlit as st

from streamlit_app.charts import Palette, active_palette

#: Semantic states a service/status pill can carry. Each ships with its own
#: glyph as well as its colour — state is never encoded by colour alone.
StatusState = Literal["ok", "warn", "error", "idle"]

_STATUS_GLYPHS: dict[StatusState, str] = {
    "ok": "●",
    "warn": "▲",
    "error": "■",
    "idle": "○",
}

_SESSION_CSS_KEY = "_theme_css_theme_type"


def _status_colours(palette: Palette) -> dict[StatusState, str]:
    """The four status hues, read straight off the shared palette."""
    return {
        "ok": palette.status_ok,
        "warn": palette.status_warn,
        "error": palette.series_negative,
        "idle": palette.text_secondary,
    }


def _stylesheet(palette: Palette) -> str:
    """Build the app stylesheet against a resolved palette."""
    status = _status_colours(palette)
    return f"""
<style>
/* Streamlit reserves a landing-page-sized gap above the first element; this is
   a dashboard, so buy some of the vertical space back.

   The floor is not a matter of taste: Streamlit's toolbar (`stHeader`) is an
   absolutely-positioned bar 56.25px tall with an *opaque* background, so any
   padding below that silently paints over the top of the first element — which
   is the page title. 4.75rem (71.25px) clears it with room to spare. Do not
   tighten this without re-measuring the toolbar. */
[data-testid="stMainBlockContainer"] {{
    padding-top: 4.75rem;
    padding-bottom: 4rem;
    max-width: 1400px;
}}

/* --- page header ------------------------------------------------------- */
.cl-header {{ margin: 0 0 1.4rem 0; }}
.cl-header .cl-title {{
    font-size: 1.95rem;
    font-weight: 700;
    line-height: 1.15;
    margin: 0.2rem 0 0 0;
    color: {palette.text_primary};
}}
.cl-header .cl-subtitle {{
    margin: 0.45rem 0 0 0;
    color: {palette.text_secondary};
    font-size: 0.92rem;
    max-width: 68ch;
    line-height: 1.5;
}}

/* --- section headers --------------------------------------------------- */
.cl-section {{ margin: 0.3rem 0 0.1rem 0; }}
.cl-section .cl-section-title {{
    font-size: 1.02rem;
    font-weight: 600;
    color: {palette.text_primary};
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
}}
.cl-section .cl-section-title::after {{
    content: "";
    flex: 1;
    height: 1px;
    background: {palette.grid};
}}
.cl-section .cl-section-caption {{
    margin: 0.3rem 0 0 0;
    font-size: 0.82rem;
    color: {palette.text_secondary};
    max-width: 78ch;
    line-height: 1.5;
}}

/* --- metric cards ------------------------------------------------------ */
[data-testid="stMetricLabel"] {{
    font-size: 0.78rem;
    font-weight: 500;
    color: {palette.text_secondary};
}}
[data-testid="stMetricValue"] {{
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
}}

/* --- pills ------------------------------------------------------------- */
.cl-pill {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.12rem 0.55rem;
    margin: 0.1rem 0.25rem 0.1rem 0;
    border-radius: 999px;
    border: 1px solid {palette.grid};
    font-size: 0.76rem;
    line-height: 1.6;
    white-space: nowrap;
    color: {palette.text_secondary};
}}
.cl-pill .cl-glyph {{ font-size: 0.6rem; }}
.cl-pill-ok {{ color: {status["ok"]}; border-color: {status["ok"]}55; }}
.cl-pill-warn {{ color: {status["warn"]}; border-color: {status["warn"]}55; }}
.cl-pill-error {{ color: {status["error"]}; border-color: {status["error"]}55; }}
.cl-pill-idle {{ color: {status["idle"]}; }}
.cl-pill-tag {{
    color: {palette.series_primary};
    border-color: {palette.series_primary}44;
    font-family: {"'JetBrains Mono', 'SFMono-Regular', Menlo, monospace"};
    font-size: 0.72rem;
}}

/* Tabs: give the active tab a real anchor rather than a faint underline. */
[data-testid="stTabs"] button[role="tab"] {{
    font-weight: 500;
    padding-left: 0.15rem;
    padding-right: 0.15rem;
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
    font-weight: 650;
}}

/* Dividers do rhythm, not decoration — keep them quiet and evenly spaced. */
[data-testid="stMainBlockContainer"] hr {{
    margin: 1.5rem 0;
    border-color: {palette.grid};
}}
</style>
"""


def inject_css() -> None:
    """Apply the stylesheet. Safe (and cheap) to call at the top of every page."""
    palette = active_palette()
    st.session_state[_SESSION_CSS_KEY] = palette.surface
    st.markdown(_stylesheet(palette), unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    """The heading block every page opens with.

    Replaces `st.title` + `st.caption` so the four tabs share one masthead
    instead of each inventing its own spacing.
    """
    st.markdown(
        f"""
<div class="cl-header">
  <h1 class="cl-title">{html.escape(title)}</h1>
  <p class="cl-subtitle">{html.escape(subtitle)}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def section(title: str, caption: str | None = None) -> None:
    """A section heading with a hairline rule and an optional standfirst."""
    caption_markup = (
        f'<p class="cl-section-caption">{caption}</p>' if caption else ""
    )
    st.markdown(
        f"""
<div class="cl-section">
  <div class="cl-section-title">{html.escape(title)}</div>
  {caption_markup}
</div>
""",
        unsafe_allow_html=True,
    )


def status_pill(label: str, state: StatusState) -> str:
    """Markup for one status pill — glyph plus label, never colour alone."""
    glyph = _STATUS_GLYPHS[state]
    return (
        f'<span class="cl-pill cl-pill-{state}">'
        f'<span class="cl-glyph">{glyph}</span>{html.escape(label)}</span>'
    )


def render_status_pill(label: str, state: StatusState) -> None:
    st.markdown(status_pill(label, state), unsafe_allow_html=True)


def tag_pills(tags: Iterable[str]) -> str:
    """Markup for a run of role tags, or an em dash when there are none."""
    rendered = "".join(
        f'<span class="cl-pill cl-pill-tag">{html.escape(tag)}</span>'
        for tag in tags
    )
    return rendered or '<span class="cl-pill cl-pill-idle">untagged</span>'


def render_tag_pills(tags: Iterable[str]) -> None:
    st.markdown(tag_pills(tags), unsafe_allow_html=True)
