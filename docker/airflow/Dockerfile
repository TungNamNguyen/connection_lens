# syntax=docker/dockerfile:1
ARG AIRFLOW_VERSION=3.1.8
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.11
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

USER airflow

# Everything the DAG imports, plus dbt — one interpreter, one lockfile.
#
# Airflow 2 could not host dbt: applying Airflow's constraints file alongside
# dbt-core ends in ResolutionImpossible, which is why this image used to carry
# a second virtualenv. uv resolves apache-airflow 3.1.8, dbt and Great
# Expectations into a single consistent set instead, so `uv.lock` replaces the
# constraints file and the extra venv is gone.
#
# Airflow itself stays out of this install: it comes from the base image and
# the `airflow` dependency group only exists so the lock resolves against it.
# --chown: the export below is written by the airflow user, not root.
COPY --chown=airflow:root pyproject.toml uv.lock /tmp/uv/
RUN cd /tmp/uv \
    && uv export --frozen --no-hashes --no-default-groups \
        --group quality --group dbt \
        --output-file /tmp/uv/requirements.txt \
    && uv pip install --python "$(command -v python)" -r /tmp/uv/requirements.txt \
    && rm -rf /tmp/uv

ENV PYTHONPATH=/opt/connection_lens \
    UV_NO_CACHE=1
