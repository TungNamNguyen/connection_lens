# Connection Lens — developer entrypoints.
# Dependencies are locked in uv.lock and installed into the project virtualenv
# (.venv) by `uv sync`; nothing is installed system-wide.

UV          := uv
VENV        := .venv
PY          := $(VENV)/bin/python
DBT         := $(VENV)/bin/dbt
PYTEST      := $(VENV)/bin/pytest
SQLFLUFF    := $(VENV)/bin/sqlfluff
RUFF        := $(VENV)/bin/ruff
STREAMLIT   := $(VENV)/bin/streamlit

DBT_ARGS    := --project-dir dbt_project --profiles-dir dbt_project
CI_DUCKDB   := $(CURDIR)/build/ci_warehouse.duckdb
DUCKDB_PATH ?= $(CURDIR)/data/warehouse/warehouse.duckdb

COMPOSE     := docker compose

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------
.PHONY: venv
venv:  ## Create .venv from uv.lock with every dependency group
	$(UV) sync --frozen

.PHONY: lock
lock:  ## Re-resolve uv.lock after editing pyproject.toml
	$(UV) lock

.PHONY: outdated
outdated:  ## Show which locked dependencies have newer releases
	$(UV) lock --upgrade --dry-run

.PHONY: dbt-deps
dbt-deps:  ## Install the dbt packages (dbt_utils, dbt_expectations)
	# Always pass the directories explicitly: dbt also reads DBT_PROJECT_DIR
	# from .env, and that relative path only resolves from the repo root.
	$(DBT) deps $(DBT_ARGS)

.PHONY: env
env:  ## Create .env from the example and pin AIRFLOW_UID to your user
	@test -f .env || cp .env.example .env
	@grep -q '^AIRFLOW_UID=' .env \
		&& sed -i 's/^AIRFLOW_UID=.*/AIRFLOW_UID=$(shell id -u)/' .env \
		|| echo "AIRFLOW_UID=$(shell id -u)" >> .env
	@echo ".env ready — edit the credentials before starting anything."

# --- quality ---------------------------------------------------------------
.PHONY: test
test:  ## Run the pytest suite
	$(PYTEST)

.PHONY: lint
lint:  ## Ruff (Python) + sqlfluff (SQL)
	$(RUFF) check .
	DUCKDB_PATH=$(CI_DUCKDB) $(SQLFLUFF) lint dbt_project/models dbt_project/snapshots dbt_project/tests

.PHONY: format
format:  ## Auto-fix formatting where it is safe
	$(RUFF) check --fix .
	DUCKDB_PATH=$(CI_DUCKDB) $(SQLFLUFF) fix --force dbt_project/models dbt_project/snapshots dbt_project/tests

.PHONY: ci-warehouse
ci-warehouse:  ## Build a throwaway warehouse from synthetic fixtures
	@mkdir -p build
	$(PY) scripts/seed_ci_warehouse.py --duckdb-path $(CI_DUCKDB) \
		--fixture tests/fixtures/connections_v1.csv --overwrite
	DUCKDB_PATH=$(CI_DUCKDB) $(DBT) build $(DBT_ARGS)
	$(PY) scripts/seed_ci_warehouse.py --duckdb-path $(CI_DUCKDB) \
		--fixture tests/fixtures/connections_v2.csv --append
	DUCKDB_PATH=$(CI_DUCKDB) $(DBT) build $(DBT_ARGS)

.PHONY: dag-check
dag-check:  ## Parse the DAG under real Airflow and assert its guard rails
	@mkdir -p build
	AIRFLOW_HOME=$(CURDIR)/build/airflow $(PY) -c "\
	from airflow.models import DagBag; \
	bag = DagBag('dags', include_examples=False); \
	assert not bag.import_errors, bag.import_errors; \
	dag = bag.dags['ingest_connections']; \
	assert dag.max_active_runs == 1 and dag.schedule is None and dag.catchup is False; \
	print(f'OK: {len(dag.tasks)} tasks parsed')"

.PHONY: check
check: lint test ci-warehouse dag-check  ## Everything CI runs, locally

# --- dbt on the real warehouse ---------------------------------------------
.PHONY: dbt-build
dbt-build:  ## Run dbt against the real warehouse
	DUCKDB_PATH=$(DUCKDB_PATH) $(DBT) build $(DBT_ARGS)

.PHONY: dbt-docs
dbt-docs:  ## Generate and serve the dbt lineage docs
	DUCKDB_PATH=$(DUCKDB_PATH) $(DBT) docs generate $(DBT_ARGS)
	DUCKDB_PATH=$(DUCKDB_PATH) $(DBT) docs serve $(DBT_ARGS)

# --- running the stack -----------------------------------------------------
.PHONY: up
up:  ## Start the whole stack (MinIO + Airflow + app)
	@mkdir -p data/warehouse   # must exist before Docker bind-mounts it
	$(COMPOSE) up -d --build
	@echo "Streamlit  http://localhost:8501"
	@echo "Airflow    http://localhost:8080"
	@echo "MinIO      http://localhost:9001"

.PHONY: down
down:  ## Stop everything (volumes are kept)
	$(COMPOSE) down

.PHONY: ps
ps:  ## Show the status of every service
	$(COMPOSE) ps

.PHONY: logs
logs:  ## Tail the Airflow scheduler log
	$(COMPOSE) logs -f airflow-scheduler

.PHONY: app
app:  ## Run Streamlit locally against the venv (no Docker)
	DUCKDB_PATH=$(DUCKDB_PATH) $(STREAMLIT) run streamlit_app/app.py

.PHONY: minio-events
minio-events:  ## Wire MinIO bucket notifications to the listener (trigger mode 2)
	$(COMPOSE) exec -T minio sh -c '\
		mc alias set local http://localhost:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD && \
		mc event add local/$${MINIO_BUCKET:-connection-lens} \
			arn:minio:sqs::CONNECTIONLENS:webhook \
			--event put --prefix raw/linkedin_connections/ --suffix .csv'
