# syntax=docker/dockerfile:1
ARG UV_VERSION=0.10.11
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens \
    UV_NO_CACHE=1

WORKDIR /opt/connection_lens

# Project dependencies plus the `listener` group: this service only translates
# a bucket notification into an Airflow REST call.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-hashes --no-default-groups --group listener \
        --output-file /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

EXPOSE 8000
CMD ["uvicorn", "services.minio_event_listener.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
