# Architecture decisions

Short records of the choices that shaped this pipeline, and what each one
rules out.

## 1. DuckDB instead of a cloud warehouse

The dataset is a few thousand rows of personal data. A cloud warehouse would
add cost, IAM surface and an exfiltration path for **no analytical benefit**.
Models are written in portable SQL and every DuckDB-specific expression is
isolated in `dbt_project/macros/duckdb_dialect.sql`, so changing adapter is a
macro rewrite, not a model rewrite.

**Consequence:** DuckDB is single-writer. Every warehouse write happens inside
the Airflow DAG, the DAG runs with `max_active_runs=1`, and Streamlit connects
read-only — in code *and* via a read-only bind mount.

## 2. MinIO as a landing zone, not a local folder

Landing raw files in object storage first is the pattern this project is meant
to demonstrate, and it keeps a complete **upload audit trail**: MinIO keeps
every uploaded object, exact duplicates included. Only Bronze de-duplicates.

## 3. Idempotency = MD5 of the file's bytes, checked against Bronze

Not the calendar date. Not the upload timestamp. Not which trigger fired.

* The owner may upload twice in one day with genuinely different content.
* The owner may re-upload the same file by accident.

Only the content can tell those apart. The check is against **Bronze** — the
dataset of record — because MinIO deliberately holds duplicates. The Streamlit
app checks the hash to warn the user, and the DAG re-checks the full MD5 after
downloading: the app layer is not trusted.

## 4. Three trigger modes, one trigger-agnostic DAG

The DAG can be started from the Airflow UI, by a MinIO bucket event, or from
the Streamlit button. Its ingestion task **never branches on which one fired**:
it always rescans the landing zone for hashes not yet in Bronze. That makes a
redundant or overlapping trigger a no-op by construction rather than by luck.

`conf.triggered_by` is written by modes 2 and 3 purely so the Job Management
tab can attribute runs — Airflow's own metadata cannot express it. The DAG
logs it and does nothing else with it.

## 5. `dim_connection` is an SCD2 snapshot with `hard_deletes='invalidate'`

Without it, a connection missing from the latest export would stay
`is_current` forever, and "who left the network" would be unanswerable.

The snapshot is fed **only the latest Silver snapshot**: `strategy='check'`
compares its input against current snapshot state, so feeding it the full
history would break the diff.

**No reason for a disappearance is inferred or stored.** LinkedIn's export
gives no signal for why someone vanished — they may have unlinked, been
removed, or deactivated. Guessing would put a fabricated fact in front of a
real outreach decision, so the model records only `dbt_valid_to` and the loss
of `is_current`.

## 6. A separate fact table at `connection_id + snapshot_ts`

SCD2 answers "what changed since we last looked". Growth and churn charts need
one row per connection per historical snapshot, which is a different grain. A
connection that disappeared simply has no row at that snapshot — no flag, and
still no reason.

## 7. Role tagging and scoring live in Python, not SQL

The Job Search tab needs interactive, target-driven filtering, and the
taxonomy is keyword logic that belongs in one testable function. Duplicating
the keyword lists in a dbt model would guarantee drift, so the SQL breakdown
mart deliberately covers only company, raw job title and connection month.

## 8. `common/` holds anything two runtimes share

The MinIO client is used by the Streamlit upload flow *and* the Airflow DAG.
The Airflow REST client is used by Streamlit *and* the event listener. Keeping
one implementation in `common/` beats two copies drifting apart in
`streamlit_app/`.

## 9. dbt runs from its own virtualenv inside the Airflow image

dbt-core and Airflow pin conflicting versions of shared libraries. Rather than
fight the resolver, dbt is installed to `/home/airflow/dbt-venv` and invoked
as a CLI from `@task.bash`. Both interpreters are pinned to the **same DuckDB
release**, because DuckDB's storage format is backward-compatible only and
both processes write to the same file.

## 10. Failing loudly beats coping quietly

An unknown column, a header that cannot be found, an object key that does not
parse, content whose hash disagrees with its key, a changed date format — all
raise. The one thing that is *not* an error is a duplicate upload, and even
that is logged explicitly rather than skipped in silence.

## 11. Airflow REST API auth: HTTP basic

Airflow 2.10's stable API (`/api/v1`) is enabled with
`airflow.api.auth.backend.basic_auth` alongside the session backend, and both
callers — the Streamlit Job Management tab and the MinIO event listener —
authenticate with the same admin credentials from `.env`.

Basic auth over localhost is proportionate here: the API is never exposed
outside the Docker network, this is a single-user tool, and a token backend
would add a refresh flow with no security benefit at this boundary. The API
version is a parameter of `AirflowClient`, so moving to Airflow 3's `/api/v2`
and its JWT flow is a change in one module.

The listener additionally accepts a shared bearer token
(`MINIO_EVENT_LISTENER_TOKEN`) so that only MinIO can ask it to start a run.
