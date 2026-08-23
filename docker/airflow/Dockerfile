# syntax=docker/dockerfile:1
ARG AIRFLOW_VERSION=2.10.5
ARG PYTHON_VERSION=3.12
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}

ARG AIRFLOW_VERSION
ARG PYTHON_VERSION

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# --- Airflow's own environment: what the DAG's Python tasks import ---------
# The shared runtime plus Great Expectations, which only the data-quality task
# needs. Constrained to Airflow's published constraints so nothing here can
# break the scheduler's dependencies.
COPY requirements.txt /tmp/requirements/
RUN pip install --no-cache-dir \
    -r /tmp/requirements/requirements.txt \
    "great_expectations~=1.3" \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# DuckDB's storage format is backward-compatible only. The DAG's Python tasks
# write Bronze and dbt writes Silver/Gold **to the same file**, so both
# interpreters must run the same DuckDB. Airflow's constraints pin an older
# release than dbt-duckdb installs, so align them explicitly here.
ARG DUCKDB_VERSION=">=1.5,<2.0"
RUN pip install --no-cache-dir --upgrade "duckdb${DUCKDB_VERSION}"

# --- dbt in an isolated virtualenv -----------------------------------------
# dbt-core pins jinja2/click/protobuf versions that clash with Airflow's, so it
# gets its own interpreter and is called as a plain CLI from @task.bash.
COPY requirements-dbt.txt /tmp/requirements/
RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir --upgrade pip \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir -r /tmp/requirements/requirements-dbt.txt

USER root
RUN ln -s /home/airflow/dbt-venv/bin/dbt /usr/local/bin/dbt
USER airflow

ENV PYTHONPATH=/opt/connection_lens \
    PIP_NO_CACHE_DIR=1
