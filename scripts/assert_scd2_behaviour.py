#!/usr/bin/env python
"""Assert the Gold layer behaves the way §14's scenario table says it should.

Runs against the **synthetic** CI warehouse after two snapshots have been
built, and checks the four outcomes that matter:

* a company change closes the old row and opens a new one (scenario 4);
* a disappearance is invalidated, with no reason recorded (scenario 5);
* joiners get their own current row (scenario 3);
* the fact table keeps one row per connection per snapshot.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from common.settings import get_settings  # noqa: E402

MOVER = "https://www.linkedin.com/in/alpha-testuser-0001"
DEPARTED = "https://www.linkedin.com/in/foxtrot-testuser-0006"
JOINER = "https://www.linkedin.com/in/juliet-testuser-0011"


def main() -> int:
    connection = duckdb.connect(str(get_settings().duckdb_file), read_only=True)

    versions = connection.execute(
        """
        select company, dbt_valid_to is null as is_current
        from gold.dim_connection
        where connection_id = ?
        order by dbt_valid_from
        """,
        [MOVER],
    ).fetchall()
    assert len(versions) == 2, f"expected two versions for the mover, got {versions}"
    assert versions[0] == ("Acme Bank (ACMB)", False), versions[0]
    assert versions[1] == ("Example Corporation", True), versions[1]

    departed = connection.execute(
        """
        select dbt_valid_to is not null as is_closed
        from gold.dim_connection
        where connection_id = ?
        """,
        [DEPARTED],
    ).fetchall()
    assert departed == [(True,)], f"hard_deletes='invalidate' did not close: {departed}"

    columns = {
        row[0]
        for row in connection.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'gold' and table_name = 'dim_connection'
            """
        ).fetchall()
    }
    forbidden = {name for name in columns if "reason" in name.lower()}
    assert not forbidden, f"no reason for a disappearance may be stored: {forbidden}"

    joiner = connection.execute(
        "select count(*) from gold.dim_connection where connection_id = ? "
        "and dbt_valid_to is null",
        [JOINER],
    ).fetchone()
    assert joiner == (1,), joiner

    fact_grain = connection.execute(
        """
        select count(*)
        from (
            select connection_id, snapshot_ts
            from gold.fct_connection_snapshot
            group by connection_id, snapshot_ts
            having count(*) > 1
        )
        """
    ).fetchone()
    assert fact_grain == (0,), "fct_connection_snapshot grain is broken"

    departed_rows = connection.execute(
        "select count(*) from gold.fct_connection_snapshot where connection_id = ?",
        [DEPARTED],
    ).fetchone()
    assert departed_rows == (1,), "a departed connection keeps its historical fact row"

    print("SCD2 behaviour matches the specification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
