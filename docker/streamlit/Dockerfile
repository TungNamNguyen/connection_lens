# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/connection_lens

WORKDIR /opt/connection_lens

# Generated from uv.lock by `make requirements` — the same pinned set every
# service installs. This image only ever reads the warehouse.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Source is bind-mounted by compose so edits show up on rerun.
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--browser.gatherUsageStats=false"]
