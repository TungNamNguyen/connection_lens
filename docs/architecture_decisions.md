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

## 9. uv.lock is the source of truth; requirements.txt is what images install

`uv.lock` pins every dependency. `make requirements` exports the runtime
groups into a committed `requirements.txt`, and the three Dockerfiles simply
`pip install -r requirements.txt`. CI runs `make requirements-check` and fails
if the file has drifted from the lock, which is the one real weakness of a
generated file that lives in the repo.

**`apache-airflow` is excluded from that export on purpose.** Exporting it too
was measured: pip then reports five conflicts against packages the base image
already ships, and quietly moves Flask 2.2.5 → 3.1.3, Flask-Limiter 3.12 → 4.1.1
and Werkzeug 2.2.3 → 3.1.8 — the exact libraries behind the auth manager that
serves `/auth/token`. Letting the base image own Airflow's own tree avoids all
of it.

Two related pins are worth knowing:

* `constraint-dependencies` holds `cryptography` in the range the base image
  was built for. The google and snowflake providers load pyOpenSSL, which
  fails to import the moment cryptography moves ahead of it, and Great
  Expectations imports the Snowflake connector on its way in.
* dbt brings `protobuf 6`, while the OpenTelemetry packages bundled with
  Airflow expect `protobuf < 5`. That conflict is real but inert: Airflow only
  imports the OTel exporter when `AIRFLOW__METRICS__OTEL_ON` is true, and it is
  false by default. Turning OTel metrics on would mean giving dbt its own
  virtualenv again.

Under Airflow 2 none of this was possible — its constraints file plus dbt-core
ends in `ResolutionImpossible`, so dbt needed a separate virtualenv regardless.

## 10. Failing loudly beats coping quietly

An unknown column, a header that cannot be found, an object key that does not
parse, content whose hash disagrees with its key, a changed date format — all
raise. The one thing that is *not* an error is a duplicate upload, and even
that is logged explicitly rather than skipped in silence.

## 11. Airflow REST API auth: JWT

Airflow 3's stable API is `/api/v2`, and it only accepts JWTs. The FAB auth
manager supplies the `/auth/token` endpoint; `AirflowClient` exchanges the
credentials from `.env` for a token once, caches it, and refreshes it
automatically when the API answers 401 or 403.

Two operational details are handled rather than left to bite:

* a freshly started API server can fail its *first* token request while FAB
  initialises its Flask app, so a 5xx on the token endpoint is retried briefly
  — a cold start must not look like an outage to the owner;
* bad credentials (4xx) fail immediately with a message naming
  `AIRFLOW_API_USERNAME` / `AIRFLOW_API_PASSWORD`, because retrying those
  helps nobody.

The listener additionally accepts a shared bearer token
(`MINIO_EVENT_LISTENER_TOKEN`) so that only MinIO can ask it to start a run.

## 12. The UI links to the address a browser can open

Inside Docker the app reaches Airflow at `http://airflow-apiserver:8080` and
MinIO at `minio:9000`. Neither name resolves in the viewer's browser, so every
clickable link and address shown in the UI comes from `AIRFLOW_PUBLIC_URL` /
`MINIO_PUBLIC_URL` instead — the API base URL is for the app, the public URL is
for the human. A page test asserts the Job Management tab never renders the
internal hostname.

The listener additionally accepts a shared bearer token
(`MINIO_EVENT_LISTENER_TOKEN`) so that only MinIO can ask it to start a run.


## 13. Deleting from the landing zone, on purpose

The original design keeps **every** uploaded object forever: MinIO is the
upload audit trail, and duplicates are retained deliberately. The Upload tab
now offers a delete button anyway, because the owner asked for one — a
personal tool should let its owner remove a file they uploaded by mistake.

The trade-off is contained rather than waved away:

* deletion is reachable **only** from the Upload tab, and the client refuses
  any key outside the configured landing-zone prefix, so the rest of the
  bucket is unreachable from the UI;
* it removes **every version** of the object. The bucket is versioned, so
  stacking a delete marker would leave the bytes on disk while telling the
  owner they were gone;
* the confirmation states plainly whether the object has been ingested. If it
  has, only the landing-zone copy goes and Bronze keeps the data; if it has
  not, the export is lost for good and the warning says so;
* every deletion is logged at WARNING level with the object key and version
  count — the audit trail loses the object, not the record that it went.

Bronze is never touched by this path, so the dataset of record and the
idempotency rules built on it are unaffected.
