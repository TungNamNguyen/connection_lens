#!/usr/bin/env python
"""Standalone runner for the Bronze -> Silver checkpoint.

The Airflow DAG calls :func:`common.data_quality.run_bronze_to_silver_checkpoint`
directly; this script is the same checkpoint on the command line, for ad-hoc
inspection of a landed snapshot::

    python great_expectations/checkpoints/bronze_to_silver.py

The suite itself lives in `common/data_quality.py` — Great Expectations 1.x
defines suites and checkpoints in code rather than in the legacy YAML store,
so there is nothing to keep in sync between the two.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.data_quality import (  # noqa: E402
    fetch_snapshot_frame,
    run_bronze_to_silver_checkpoint,
)
from common.duckdb_io import connect_read_only  # noqa: E402
from common.settings import get_settings  # noqa: E402

logger = logging.getLogger("bronze_to_silver")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="Warehouse file (default: DUCKDB_PATH from .env).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    duckdb_path = args.duckdb_path or get_settings().duckdb_file

    with connect_read_only(duckdb_path) as connection:
        frame = fetch_snapshot_frame(connection)

    report = run_bronze_to_silver_checkpoint(frame)
    logger.info(report.summary_line)
    if not report.success:
        logger.error("Failed expectations: %s", ", ".join(report.failed_expectations))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
