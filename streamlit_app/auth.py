"""Login gate for the Streamlit app.

Every tab reads real personal data (§1), so the whole app sits behind a
single owner account whose credentials come from `.env` like every other
secret — never hardcoded here (§17).

Two deliberate choices:

* the comparison is a **pure function** (`verify_credentials`), so the only
  security-relevant logic in this file is unit-testable without Streamlit;
* the gate is **fail-closed** — with no credentials configured the app
  refuses to render rather than quietly running open.

Streamlit builds its own navigation from `pages/`, and a page can be opened
by URL directly, so `require_login()` runs at the top of *every* page rather
than only in `app.py`.
"""

from __future__ import annotations

import secrets

import streamlit as st

from common.settings import Settings, get_settings
from streamlit_app.ui import APP_TITLE, PRIVACY_NOTE

#: Set to the signed-in username. Streamlit keeps session state server-side
#: and per browser session, so nothing authenticating lives in the client.
SESSION_USER_KEY = "auth_username"

#: Hides the page navigation while signed out, so the tab names are not
#: readable before authenticating. Cosmetic only — every page runs the gate
#: itself, so opening one by URL is stopped just the same.
_HIDE_NAV_CSS = """
<style>
[data-testid="stSidebarNav"] { display: none; }
</style>
"""


def verify_credentials(
    username: str,
    password: str,
    *,
    expected_username: str,
    expected_password: str,
) -> bool:
    """Check a submitted username/password pair in constant time.

    An unset expectation never matches, so a missing `.env` entry can never
    turn into an empty password that lets anyone in.
    """
    if not expected_username or not expected_password:
        return False
    username_ok = secrets.compare_digest(
        username.encode("utf-8"), expected_username.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        password.encode("utf-8"), expected_password.encode("utf-8")
    )
    # Both comparisons always run: short-circuiting would leak which half
    # was wrong through the response time.
    return username_ok and password_ok


def current_user() -> str | None:
    """The signed-in username, or None."""
    return st.session_state.get(SESSION_USER_KEY)


def require_login() -> None:
    """Stop the page unless this session has signed in.

    Call it directly after `configure_page()` — `st.set_page_config()` has to
    be the first Streamlit call on a page.
    """
    settings = get_settings()

    if not settings.has_app_credentials:
        st.error(
            "**Login is not configured.** Set `STREAMLIT_AUTH_USERNAME` and "
            "`STREAMLIT_AUTH_PASSWORD` in `.env` (see `.env.example`), then "
            "restart the app.",
            icon="🔒",
        )
        st.stop()

    if current_user():
        _render_account_controls()
        return

    st.markdown(_HIDE_NAV_CSS, unsafe_allow_html=True)
    _render_login_form(settings)
    st.stop()


def _render_login_form(settings: Settings) -> None:
    """The sign-in screen shown in place of the requested page."""
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title(f"🔗 {APP_TITLE}")
        st.caption("Sign in to reach your network data.")

        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            if verify_credentials(
                username,
                password,
                expected_username=settings.streamlit_auth_username,
                expected_password=settings.streamlit_auth_password.get_secret_value(),
            ):
                st.session_state[SESSION_USER_KEY] = username
                st.rerun()
            else:
                # One message for both halves — never reveal which was wrong.
                st.error("Wrong username or password.", icon="🚫")

        st.caption(PRIVACY_NOTE)


def _render_account_controls() -> None:
    """Who is signed in, plus the way out, at the top of the sidebar."""
    with st.sidebar:
        st.caption(f"Signed in as **{current_user()}**")
        if st.button("Sign out", key="sign_out", use_container_width=True):
            st.session_state.pop(SESSION_USER_KEY, None)
            st.rerun()
        st.divider()
