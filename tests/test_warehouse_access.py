"""Reading the warehouse while the ingestion DAG holds it (§10).

DuckDB allows either one read-write process or several read-only ones, never
both, so every Streamlit read fails for as long as a DAG run is writing. The
lock is taken by a **real second process** here rather than by a mocked
exception: the behaviour under test belongs to DuckDB, so a fake would keep
passing even if DuckDB stopped raising.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from common.duckdb_io import connect_read_only
from common.errors import WarehouseBusyError
from common.settings import get_settings
from streamlit_app import db

#: Opens the warehouse read-write, announces itself, then holds the lock.
_HOLDER = """
import sys, time
import duckdb

connection = duckdb.connect(sys.argv[1])
connection.execute("create table if not exists held (a integer)")
print("ready", flush=True)
time.sleep(float(sys.argv[2]))
"""

HOLD_SECONDS = 30
READY_TIMEOUT_SECONDS = 30


@pytest.fixture
def locked_warehouse(tmp_path: Path) -> Iterator[Path]:
    """A warehouse file with its writer lock held by another process."""
    warehouse = tmp_path / "warehouse.duckdb"
    duckdb.connect(str(warehouse)).close()

    script = tmp_path / "holder.py"
    script.write_text(_HOLDER)
    process = subprocess.Popen(
        [sys.executable, str(script), str(warehouse), str(HOLD_SECONDS)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    if process.stdout.readline().strip() != "ready":
        process.kill()
        pytest.fail("the lock holder never started")
    try:
        yield warehouse
    finally:
        process.terminate()
        process.wait(timeout=READY_TIMEOUT_SECONDS)


@pytest.fixture
def busy_settings(
    locked_warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point the app's settings at the locked warehouse."""
    monkeypatch.setenv("DUCKDB_PATH", str(locked_warehouse))
    get_settings.cache_clear()
    yield locked_warehouse
    get_settings.cache_clear()


def test_a_locked_warehouse_raises_warehouse_busy_not_an_io_error(
    locked_warehouse: Path,
) -> None:
    """The DuckDB lock is translated at the only layer that can see it."""
    with pytest.raises(WarehouseBusyError) as raised, connect_read_only(locked_warehouse):
        pass
    assert "ingestion" in str(raised.value).lower()


def test_warehouse_status_reports_busy_rather_than_missing(
    busy_settings: Path,
) -> None:
    """`busy` and "never ingested" are different answers and must stay so.

    Reporting a locked warehouse as absent is what made the app offer its
    first-run onboarding message in the middle of an ingestion run.
    """
    status = db.warehouse_status()
    assert status["busy"] is True
    assert status["warehouse"] is False


def test_safe_query_degrades_instead_of_crashing(busy_settings: Path) -> None:
    """Every page-level read returns empty rather than raising."""
    assert db.safe_query("select 1 as one").empty


def test_reads_recover_once_the_lock_is_released(tmp_path: Path) -> None:
    """The busy state is transient — nothing latches after the run finishes."""
    warehouse = tmp_path / "warehouse.duckdb"
    writer = duckdb.connect(str(warehouse))
    writer.execute("create table t (a integer); insert into t values (1)")
    writer.close()

    with connect_read_only(warehouse) as connection:
        assert connection.execute("select count(*) from t").fetchone() == (1,)
