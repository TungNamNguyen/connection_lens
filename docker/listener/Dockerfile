# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens

WORKDIR /opt/connection_lens

# The shared runtime plus the web server. This service only translates a
# bucket notification into an Airflow REST call.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    "fastapi~=0.115" \
    "uvicorn[standard]~=0.32"

EXPOSE 8000
CMD ["uvicorn", "services.minio_event_listener.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
