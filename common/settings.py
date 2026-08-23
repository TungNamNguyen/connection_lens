"""Environment-driven configuration.

No credential and no absolute path is ever hardcoded in this project (§17);
everything below comes from `.env` (see `.env.example`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve(path: str | Path) -> Path:
    """Resolve a possibly-relative configured path against the repo root."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- MinIO -------------------------------------------------------------
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    minio_secure: bool = False
    minio_bucket: str = "connection-lens"
    minio_raw_prefix: str = "raw/linkedin_connections"

    # --- DuckDB ------------------------------------------------------------
    duckdb_path: Path = Path("./data/warehouse/warehouse.duckdb")

    # --- Airflow REST API --------------------------------------------------
    airflow_api_base_url: str = "http://localhost:8080"
    airflow_api_username: str = "airflow"
    airflow_api_password: SecretStr = SecretStr("")
    airflow_dag_id: str = "ingest_connections"
    airflow_ingestion_task_id: str = "ingest_new_objects_to_bronze"
    airflow_api_timeout_seconds: float = 15.0

    # --- MinIO event listener ---------------------------------------------
    minio_event_listener_token: SecretStr = SecretStr("")

    # --- dbt ---------------------------------------------------------------
    dbt_project_dir: Path = Path("./dbt_project")
    dbt_profiles_dir: Path = Path("./dbt_project")
    dbt_target: str = "dev"

    log_level: str = Field(default="INFO")

    @property
    def duckdb_file(self) -> Path:
        """Absolute path of the warehouse file."""
        return _resolve(self.duckdb_path)

    @property
    def dbt_project_path(self) -> Path:
        return _resolve(self.dbt_project_dir)

    @property
    def dbt_profiles_path(self) -> Path:
        return _resolve(self.dbt_profiles_dir)

    @property
    def has_minio_credentials(self) -> bool:
        return bool(
            self.minio_access_key.get_secret_value()
            and self.minio_secret_key.get_secret_value()
        )

    @property
    def has_airflow_credentials(self) -> bool:
        return bool(self.airflow_api_username and self.airflow_api_password.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
