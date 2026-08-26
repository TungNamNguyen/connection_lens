"""Small presentation helpers shared by the Streamlit pages."""

from __future__ import annotations

import urllib.parse
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from common.errors import ConnectionLensError
from common.minio_client import LandingZoneClient, LandingZoneStatus
from common.settings import get_settings

PAGE_ICON = "🔗"
APP_TITLE = "Connection Lens"

PRIVACY_NOTE = (
    "Local-only, single-user tool. This app reads your own LinkedIn data "
    "export from a DuckDB file on this machine — nothing is sent anywhere."
)


def configure_page(subtitle: str, *, layout: str = "wide") -> None:
    """Apply the shared page config and header."""
    st.set_page_config(
        page_title=f"{APP_TITLE} — {subtitle}", page_icon=PAGE_ICON, layout=layout
    )


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
    """Consistent privacy reminder in the sidebar."""
    with st.sidebar:
        st.divider()
        st.caption(PRIVACY_NOTE)


def require_warehouse(status: dict[str, bool], layer: str, hint: str) -> None:
    """Stop the page with a helpful message when a warehouse layer is missing."""
    if status.get(layer):
        return
    st.info(
        f"**Nothing to show yet.** {hint}",
        icon="⏳",
    )
    st.stop()
