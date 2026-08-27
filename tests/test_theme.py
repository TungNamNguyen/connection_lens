"""The visual system's pure parts (§9).

The stylesheet itself is generated, not hand-written, so the thing worth testing
is that it is generated *from the shared palette* — a hard-coded hex creeping
into `theme.py` is exactly the drift this module exists to prevent.
"""

from __future__ import annotations

import pytest

from streamlit_app.charts import DARK_PALETTE, LIGHT_PALETTE, Palette
from streamlit_app.theme import _stylesheet, status_pill, tag_pills


@pytest.mark.parametrize("palette", [LIGHT_PALETTE, DARK_PALETTE])
def test_the_stylesheet_is_built_from_the_shared_palette(palette: Palette) -> None:
    """Chrome and charts must never drift onto different blues."""
    # `surface` is deliberately absent: backgrounds are owned by the tokens in
    # `.streamlit/config.toml`, so the stylesheet never repaints them.
    css = _stylesheet(palette)
    for colour in (
        palette.text_primary,
        palette.text_secondary,
        palette.grid,
        palette.series_primary,
        palette.status_ok,
        palette.status_warn,
    ):
        assert colour in css, f"{colour} missing from the generated stylesheet"


def test_light_and_dark_stylesheets_differ() -> None:
    """Dark mode is a selected palette, not the same CSS with a flag."""
    assert _stylesheet(LIGHT_PALETTE) != _stylesheet(DARK_PALETTE)


@pytest.mark.parametrize("state", ["ok", "warn", "error", "idle"])
def test_status_pills_carry_a_glyph_as_well_as_a_colour(state: str) -> None:
    """State is never encoded by colour alone."""
    markup = status_pill("Healthy", state)  # type: ignore[arg-type]
    assert f"cl-pill-{state}" in markup
    assert "cl-glyph" in markup
    assert "Healthy" in markup


def test_pill_labels_are_escaped() -> None:
    """Company and position text reaches these helpers straight from the export."""
    assert "<script>" not in status_pill("<script>alert(1)</script>", "ok")
    assert "&lt;script&gt;" in tag_pills(["<script>"])


def test_no_tags_renders_an_explicit_untagged_pill() -> None:
    """An empty run of pills would read as missing data rather than as none."""
    assert "untagged" in tag_pills([])
    assert "recruiter_talent" in tag_pills(["recruiter_talent"])
