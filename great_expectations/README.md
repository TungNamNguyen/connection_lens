# Great Expectations

The Bronze → Silver expectation suite lives in
[`common/data_quality.py`](../common/data_quality.py), not in this directory.
Great Expectations 1.x defines suites, validation definitions and checkpoints
**in code** against an ephemeral context, so there is no YAML store to keep in
sync with the Python that runs it.

What is here:

* [`checkpoints/bronze_to_silver.py`](checkpoints/bronze_to_silver.py) — a
  command-line runner for the same checkpoint the Airflow DAG executes:

  ```bash
  python great_expectations/checkpoints/bronze_to_silver.py
  ```

> **Do not add an `__init__.py` to this directory.** The repository root is on
> `PYTHONPATH`, and a regular package named `great_expectations` here would
> shadow the installed library. Without one, Python treats this directory as a
> namespace portion and the real package still wins.
