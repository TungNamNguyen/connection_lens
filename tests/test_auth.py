"""The Streamlit login gate (§9).

The credential comparison is a pure function on purpose, so the only
security-relevant logic in the app can be tested without a browser or a
running Streamlit server.
"""

from __future__ import annotations

import pytest

from common.settings import Settings
from streamlit_app.auth import verify_credentials

OWNER = "owner"
SECRET = "correct-horse-battery-staple"


def test_the_configured_pair_is_accepted() -> None:
    assert verify_credentials(
        OWNER, SECRET, expected_username=OWNER, expected_password=SECRET
    )


@pytest.mark.parametrize(
    ("username", "password"),
    [
        (OWNER, "wrong"),
        ("someone-else", SECRET),
        ("someone-else", "wrong"),
        ("", ""),
        (OWNER.upper(), SECRET),  # the comparison is case-sensitive
        (OWNER, SECRET + " "),  # ...and whitespace-sensitive
    ],
)
def test_anything_else_is_rejected(username: str, password: str) -> None:
    assert not verify_credentials(
        username, password, expected_username=OWNER, expected_password=SECRET
    )


@pytest.mark.parametrize(
    ("expected_username", "expected_password"),
    [("", ""), (OWNER, ""), ("", SECRET)],
)
def test_an_unconfigured_login_never_matches(
    expected_username: str, expected_password: str
) -> None:
    """A missing `.env` entry must not become an empty password that lets anyone in."""
    assert not verify_credentials(
        expected_username,
        expected_password,
        expected_username=expected_username,
        expected_password=expected_password,
    )


def test_non_ascii_credentials_are_compared_not_crashed() -> None:
    """`secrets.compare_digest` rejects non-ASCII `str`, so both sides are encoded."""
    password = "mật-khẩu-của-tôi"
    assert verify_credentials(
        OWNER, password, expected_username=OWNER, expected_password=password
    )
    assert not verify_credentials(
        OWNER, "mật-khẩu-khác", expected_username=OWNER, expected_password=password
    )


def test_settings_report_whether_the_login_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STREAMLIT_AUTH_USERNAME", OWNER)
    monkeypatch.setenv("STREAMLIT_AUTH_PASSWORD", SECRET)
    assert Settings().has_app_credentials

    monkeypatch.setenv("STREAMLIT_AUTH_PASSWORD", "")
    assert not Settings().has_app_credentials
