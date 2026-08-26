"""Smoke-test every Streamlit page by actually running it (§9).

`AppTest` executes a page headlessly, so a broken widget call, a bad column
config or a crash in an empty-warehouse branch fails here instead of in the
browser. Pages are run twice: against a missing warehouse (the state right
after a fresh clone) and, when one is available, against a built one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from common.settings import get_settings
from streamlit_app import db
from streamlit_app.auth import SESSION_USER_KEY

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    "streamlit_app/app.py",
    "streamlit_app/pages/1_upload.py",
    "streamlit_app/pages/2_network_stats.py",
    "streamlit_app/pages/3_job_search.py",
    "streamlit_app/pages/4_job_management.py",
]

RUN_TIMEOUT_SECONDS = 30


APP_USERNAME = "owner"
APP_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def configured_login(monkeypatch: pytest.MonkeyPatch):
    """Every page sits behind the login gate, so give the gate a login to check.

    Without this the app fails closed and no page renders — which is the
    intended behaviour, covered by `test_the_app_refuses_to_render_...`.
    """
    monkeypatch.setenv("STREAMLIT_AUTH_USERNAME", APP_USERNAME)
    monkeypatch.setenv("STREAMLIT_AUTH_PASSWORD", APP_PASSWORD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def run_page(path: str, *, signed_in: bool = True) -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / path), default_timeout=RUN_TIMEOUT_SECONDS)
    if signed_in:
        # Streamlit keeps session state server-side, so seeding it is exactly
        # what a successful sign-in leaves behind.
        app.session_state[SESSION_USER_KEY] = APP_USERNAME
    app.run()
    return app


def password_inputs(app: AppTest) -> list[str]:
    return [item.label for item in app.text_input]


def assert_no_exception(app: AppTest, path: str) -> None:
    assert not app.exception, (
        f"{path} raised: " + "; ".join(str(item.value) for item in app.exception)
    )


@pytest.fixture
def empty_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the app at a warehouse that does not exist yet."""
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("MINIO_ACCESS_KEY", "")
    monkeypatch.setenv("MINIO_SECRET_KEY", "")
    monkeypatch.setenv("AIRFLOW_API_PASSWORD", "")
    get_settings.cache_clear()
    # `st.cache_data` results outlive a single AppTest run, so a stale
    # "warehouse missing" answer would leak into the next test.
    db.clear_caches()
    yield
    get_settings.cache_clear()
    db.clear_caches()


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_before_the_first_ingestion(path: str, empty_warehouse: None) -> None:
    """Nothing may crash when the warehouse and services are not there yet."""
    app = run_page(path)
    assert_no_exception(app, path)


@pytest.fixture
def built_warehouse(monkeypatch: pytest.MonkeyPatch) -> Path:
    """A warehouse with Gold and marts built, if the environment provides one."""
    configured = os.environ.get("SMOKE_TEST_DUCKDB_PATH")
    if not configured or not Path(configured).exists():
        pytest.skip(
            "Set SMOKE_TEST_DUCKDB_PATH to a built warehouse to run the "
            "with-data page smoke tests (see `make ci-warehouse`)."
        )
    monkeypatch.setenv("DUCKDB_PATH", configured)
    get_settings.cache_clear()
    db.clear_caches()
    yield Path(configured)
    get_settings.cache_clear()
    db.clear_caches()


@pytest.mark.parametrize(
    "path",
    ["streamlit_app/app.py", "streamlit_app/pages/2_network_stats.py",
     "streamlit_app/pages/3_job_search.py"],
)
def test_pages_render_with_data(path: str, built_warehouse: Path) -> None:
    app = run_page(path)
    assert_no_exception(app, path)


def test_job_search_ranks_without_any_configuration(built_warehouse: Path) -> None:
    """No target company to type: the ranking is meaningful on arrival."""
    app = run_page("streamlit_app/pages/3_job_search.py")
    assert_no_exception(app, "3_job_search.py")

    labels = {item.label for item in app.text_input}
    assert "Target company" not in labels, "the target company input was removed"
    assert "Company contains" in labels, "company filtering stays"

    scores = app.dataframe[0].value["Score"]
    assert scores.max() > 0, "somebody must score without a target company"
    assert scores.is_monotonic_decreasing, "the table is sorted by referral strength"


def test_job_search_can_search_names_and_positions(built_warehouse: Path) -> None:
    """One box over both fields — you either remember the person or the job."""
    app = run_page("streamlit_app/pages/3_job_search.py")
    before = len(app.dataframe[0].value)

    search = next(
        item for item in app.text_input if item.label == "Name or position contains"
    )
    search.set_value("engineer").run()
    assert_no_exception(app, "3_job_search.py (searched)")

    table = app.dataframe[0].value
    assert 0 < len(table) < before
    matched = table["Name"].fillna("") + " " + table["Position"].fillna("")
    assert matched.str.lower().str.contains("engineer").all()


def test_job_search_company_filter_narrows_the_table(built_warehouse: Path) -> None:
    app = run_page("streamlit_app/pages/3_job_search.py")
    before = len(app.dataframe[0].value)

    company = next(item for item in app.text_input if item.label == "Company contains")
    company.set_value("acme").run()
    assert_no_exception(app, "3_job_search.py (filtered)")
    assert len(app.dataframe[0].value) < before


def test_airflow_link_points_at_the_browser_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside Docker the API host is unreachable from the viewer's browser.

    The Job Management tab talks to `airflow-apiserver:8080` but must *link* to
    the address a human can actually open.
    """
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("AIRFLOW_API_BASE_URL", "http://airflow-apiserver:8080")
    monkeypatch.setenv("AIRFLOW_PUBLIC_URL", "http://localhost:8080")
    monkeypatch.setenv("AIRFLOW_API_PASSWORD", "not-a-real-password")
    get_settings.cache_clear()
    db.clear_caches()
    try:
        app = run_page("streamlit_app/pages/4_job_management.py")
        assert_no_exception(app, "4_job_management.py")

        rendered = " ".join(str(item.value) for item in app.markdown)
        assert "http://localhost:8080/dags/ingest_connections" in rendered
        assert "airflow-apiserver" not in rendered
    finally:
        get_settings.cache_clear()
        db.clear_caches()


def test_upload_page_survives_an_unreachable_minio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With credentials set but MinIO down, the tab must explain, not crash.

    The MinIO SDK raises `urllib3.exceptions.MaxRetryError` here, which is
    neither an `S3Error` nor an `OSError`; before it was translated it escaped
    the page's error handling as a traceback.
    """
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "connection-lens")
    monkeypatch.setenv("MINIO_SECRET_KEY", "not-a-real-secret")
    get_settings.cache_clear()
    db.clear_caches()
    st.cache_resource.clear()
    try:
        app = run_page("streamlit_app/pages/1_upload.py")
        assert_no_exception(app, "1_upload.py (MinIO down)")
        assert app.error, "the page must tell the owner the landing zone is unavailable"
        assert "Landing zone unavailable" in app.error[0].value
    finally:
        get_settings.cache_clear()
        db.clear_caches()
        st.cache_resource.clear()


@pytest.mark.parametrize("path", PAGES)
def test_no_page_renders_without_signing_in(path: str, empty_warehouse: None) -> None:
    """Opening any page by URL must land on the sign-in form, not on the data."""
    app = run_page(path, signed_in=False)
    assert_no_exception(app, path)
    assert "Password" in password_inputs(app), (
        f"{path} rendered without the login gate"
    )


def test_the_right_password_signs_in_and_the_wrong_one_does_not(
    empty_warehouse: None,
) -> None:
    app = run_page("streamlit_app/app.py", signed_in=False)
    username, password = app.text_input[0], app.text_input[1]

    username.set_value(APP_USERNAME)
    password.set_value("not-the-password")
    app.button[0].click().run()
    assert_no_exception(app, "app.py (wrong password)")
    assert app.error, "a failed sign-in must say so"
    assert SESSION_USER_KEY not in app.session_state

    app.text_input[0].set_value(APP_USERNAME)
    app.text_input[1].set_value(APP_PASSWORD)
    app.button[0].click().run()
    assert_no_exception(app, "app.py (correct password)")
    assert app.session_state[SESSION_USER_KEY] == APP_USERNAME
    assert "Password" not in password_inputs(app), "the form must be gone once signed in"


def test_the_app_refuses_to_render_when_no_login_is_configured(
    empty_warehouse: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: an unset `.env` entry must never leave the app open (§1)."""
    monkeypatch.setenv("STREAMLIT_AUTH_USERNAME", "")
    monkeypatch.setenv("STREAMLIT_AUTH_PASSWORD", "")
    get_settings.cache_clear()

    app = run_page("streamlit_app/app.py", signed_in=True)
    assert_no_exception(app, "app.py (login unconfigured)")
    assert app.error, "the app must explain that the login is not configured"
    assert "STREAMLIT_AUTH_USERNAME" in app.error[0].value
