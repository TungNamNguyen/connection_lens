"""Small presentation helpers shared by the Streamlit pages."""

from __future__ import annotations

import unicodedata
import urllib.parse
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from common.errors import ConnectionLensError
from common.minio_client import LandingZoneClient, LandingZoneStatus
from common.settings import get_settings
from streamlit_app.theme import inject_css

PAGE_ICON = "🔗"
APP_TITLE = "Connection Lens"


def configure_page(subtitle: str, *, layout: str = "wide") -> None:
    """Apply the shared page config and stylesheet.

    Call it as the first Streamlit call on every page: `st.set_page_config()`
    has to come before anything else renders, and the stylesheet has to be in
    place before the first element it styles.
    """
    st.set_page_config(
        page_title=f"{APP_TITLE} — {subtitle}", page_icon=PAGE_ICON, layout=layout
    )
    inject_css()


#: Vietnamese "đ" is a letter in its own right, not an accented "d", so NFD
#: leaves it alone — it has to be mapped by hand.
_FOLD_EXCEPTIONS = str.maketrans({"đ": "d", "Đ": "D", "ð": "d"})


def fold_accents(value: object) -> str:
    """Lower-case a string and strip diacritics, for accent-blind search.

    Half this network has Vietnamese names, so typing "nguyen" has to find
    "Nguyễn" and "ngan hang" has to find "Ngân hàng".
    """
    if value is None:
        return ""
    text = str(value).translate(_FOLD_EXCEPTIONS)
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_marks.casefold()


def display_profile_url(connection_id: str | None) -> str:
    """Percent-decode a LinkedIn URL for display only.

    The stored `connection_id` always stays in its raw percent-encoded form —
    it is the key that joins every layer together (§7).
    """
    if not connection_id:
        return ""
    return urllib.parse.unquote(connection_id)


def format_timestamp(value: object) -> str:
    """Render a timestamp compactly, tolerating NaT/None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, str):
        return value
    if pd.isna(value):  # type: ignore[arg-type]
        return "—"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def format_date(value: object) -> str:
    """Render a date as YYYY-MM-DD, tolerating NaT/None."""
    if value is None:
        return "—"
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError):
        return "—"
    return "—" if pd.isna(stamp) else stamp.strftime("%Y-%m-%d")


def _text_or_dash(value: object) -> str:
    """A trimmed string, or an em dash for anything missing/blank."""
    if value is None:
        return "—"
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return "—"
    except (TypeError, ValueError):
        pass
    return str(value).strip() or "—"


def format_change(previous: object, current: object) -> str:
    """Render `old → new` when a value moved, or the value alone when it did not.

    A connection can change company, title, or both in the same export. Putting
    an arrow on the field that did not move would invent a change that never
    happened, so an unchanged value is rendered plainly.
    """
    previous_text = _text_or_dash(previous)
    current_text = _text_or_dash(current)
    if previous_text == current_text:
        return current_text
    return f"{previous_text} → {current_text}"


def format_duration(seconds: float | None) -> str:
    """Render a duration in seconds as `1m 04s`."""
    if seconds is None or pd.isna(seconds):
        return "—"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}m {remainder:02d}s" if minutes else f"{remainder}s"


def utc_now() -> datetime:
    return datetime.now(UTC)


@st.cache_resource(show_spinner=False)
def landing_zone_client() -> LandingZoneClient:
    """One MinIO client per session."""
    return LandingZoneClient.from_settings()


def minio_status() -> LandingZoneStatus:
    """Describe the landing zone for the status badges.

    "MinIO is down" and "the bucket does not exist yet" are reported
    separately: only the first one makes the Upload tab unusable.
    """
    settings = get_settings()
    if not settings.has_minio_credentials:
        return LandingZoneStatus(
            reachable=False,
            bucket_exists=False,
            detail="No MinIO credentials configured — copy `.env.example` to `.env`.",
        )
    try:
        client = landing_zone_client()
    except ConnectionLensError as error:
        return LandingZoneStatus(reachable=False, bucket_exists=False, detail=str(error))

    status = client.check_status()
    if status.is_ready:
        return replace(
            status,
            detail=f"Console {settings.minio_public_url} · bucket `{settings.minio_bucket}`",
        )
    return status


def render_sidebar_footer() -> None:
    """Bottom of the sidebar, shared by every page."""
    with st.sidebar:
        st.divider()


def require_warehouse(status: dict[str, bool], layer: str, hint: str) -> None:
    """Stop the page with a helpful message when a warehouse layer is missing."""
    if status.get(layer):
        return
    st.info(
        f"**Nothing to show yet.** {hint}",
        icon="⏳",
    )
    st.stop()
