# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens

WORKDIR /opt/connection_lens

# Generated from uv.lock by `make requirements`. This service only translates a
# bucket notification into an Airflow REST call.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "services.minio_event_listener.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
