# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens

WORKDIR /opt/connection_lens

# The shared runtime plus the UI libraries. Deliberately no dbt, no Airflow
# and no Great Expectations: this image only ever reads the warehouse.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    "streamlit~=1.40" \
    "altair~=5.4"

# Source is bind-mounted by compose so edits show up on rerun.
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--browser.gatherUsageStats=false"]
