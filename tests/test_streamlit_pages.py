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
from streamlit.testing.v1 import AppTest

from common.settings import get_settings
from streamlit_app import db

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    "streamlit_app/app.py",
    "streamlit_app/pages/1_upload.py",
    "streamlit_app/pages/2_network_stats.py",
    "streamlit_app/pages/3_job_search.py",
    "streamlit_app/pages/4_job_management.py",
]

RUN_TIMEOUT_SECONDS = 30


def run_page(path: str) -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / path), default_timeout=RUN_TIMEOUT_SECONDS)
    app.run()
    return app


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


def test_job_search_scores_when_a_target_company_is_set(built_warehouse: Path) -> None:
    app = run_page("streamlit_app/pages/3_job_search.py")
    assert_no_exception(app, "3_job_search.py")
    target = [item for item in app.text_input if item.label == "Target company"]
    assert target, "the Job Search tab must expose a target-company input"
    target[0].set_value("Example Corporation").run()
    assert_no_exception(app, "3_job_search.py (scored)")


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
