# syntax=docker/dockerfile:1
ARG UV_VERSION=0.10.11
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens \
    UV_NO_CACHE=1

WORKDIR /opt/connection_lens

# Project dependencies plus the `app` group only. Deliberately no dbt, no
# Airflow and no Great Expectations: this image only ever reads the warehouse.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-hashes --no-default-groups --group app \
        --output-file /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Source is bind-mounted by compose so edits show up on rerun.
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--browser.gatherUsageStats=false"]
