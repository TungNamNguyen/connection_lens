"""Shared, dependency-light building blocks used by more than one runtime.

Anything imported by at least two of {Streamlit app, Airflow DAG, MinIO event
listener} lives here so the logic exists exactly once and stays unit-testable
in isolation (see `tests/`).
"""
