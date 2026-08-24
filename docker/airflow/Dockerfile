# syntax=docker/dockerfile:1
ARG AIRFLOW_VERSION=3.1.8
ARG PYTHON_VERSION=3.12
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# requirements.txt is generated from uv.lock by `make requirements` and checked
# for drift in CI. It carries the runtime groups only — apache-airflow is not
# in it on purpose: Airflow comes from this base image, and reinstalling it
# from the lock moves flask, werkzeug and the OpenTelemetry stack out from
# under the providers the image already ships.
#
# Airflow 2 could not host dbt at all (its constraints file plus dbt-core ends
# in ResolutionImpossible), which is why this image used to carry a second
# virtualenv. On Airflow 3 they share one interpreter.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir --user -r /requirements.txt

ENV PYTHONPATH=/opt/connection_lens
