# Hướng dẫn build Connection Lens từ đầu

Tài liệu này mô tả **thứ tự dựng lại toàn bộ dự án từ một thư mục trống**, theo
đúng trình tự đã dùng để xây nó. Mỗi giai đoạn nêu: mục tiêu, file tạo ra, quyết
định thiết kế đứng sau, cách kiểm chứng trước khi đi tiếp, và commit tương ứng.

> **Nguyên tắc xuyên suốt:** dự án xử lý PII thật (tên, email, URL LinkedIn,
> công ty). Không commit dữ liệu thật, không scraping, không dịch vụ cloud. Mọi
> ví dụ và fixture đều là dữ liệu tổng hợp. Xem §1 của `.claude/CLAUDE.md`.

**Thứ tự build không tùy tiện.** Nó đi từ dưới lên: hạ tầng cấu hình → thư viện
dùng chung (`common/`) → orchestration → transformation → serving → đóng gói →
CI. Lý do: `common/` được **hai runtime** dùng chung (Airflow DAG và Streamlit),
nên nó phải tồn tại và có test trước khi hai lớp trên được viết.

---

## Mục lục

| Giai đoạn | Nội dung | Kết quả kiểm chứng được |
| --- | --- | --- |
| [0](#giai-đoạn-0--khung-repo-và-ranh-giới-bảo-mật) | Khung repo, `.gitignore`, `.env.example` | `git status` sạch, không lộ PII |
| [1](#giai-đoạn-1--quản-lý-dependency-bằng-uv) | `pyproject.toml`, `uv.lock`, `requirements.txt` | `uv sync --frozen` chạy được |
| [2](#giai-đoạn-2--common--lõi-dùng-chung-của-hai-runtime) | `common/` — 11 module thuần Python | `pytest tests/test_hashing.py …` xanh |
| [3](#giai-đoạn-3--airflow-dag-ingestion) | `dags/ingest_connections_dag.py` | `make dag-check` |
| [4](#giai-đoạn-4--dbt-silver--gold--marts) | `dbt_project/` | `dbt build` xanh trên fixture |
| [5](#giai-đoạn-5--great-expectations-checkpoint) | `great_expectations/` | checkpoint CLI chạy |
| [6](#giai-đoạn-6--streamlit-4-tab) | `streamlit_app/` | `make app` mở được 4 tab |
| [7](#giai-đoạn-7--fastapi-event-listener-trigger-mode-2) | `services/minio_event_listener/` | `GET /health` trả `ok` |
| [8](#giai-đoạn-8--test-suite-và-fixture-tổng-hợp) | `tests/`, `scripts/` | `pytest` toàn bộ xanh |
| [9](#giai-đoạn-9--docker-hóa-toàn-bộ-stack) | `docker/`, `docker-compose.yml`, `Makefile` | `make up` lên đủ service |
| [10](#giai-đoạn-10--ci-trên-github-actions) | `.github/workflows/ci.yml` | 3 job xanh trên PR |
| [11](#giai-đoạn-11--tài-liệu) | `README.md`, `docs/` | — |
| [12](#giai-đoạn-12--chạy-thử-end-to-end) | Nghiệm thu 11 kịch bản idempotency | — |

---

## Điều kiện tiên quyết

| Công cụ | Phiên bản | Vì sao |
| --- | --- | --- |
| Python | **3.12+** | `pyproject.toml` yêu cầu `>=3.12`; image Airflow cũng là 3.12 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 0.10.11 (pin trong CI) | Quản lý venv + lockfile |
| Docker + Docker Compose v2 | mới | MinIO, Airflow 3, Streamlit, listener |
| `make`, `git` | — | Entry point dev |

**Mọi lệnh Python đều chạy trong `.venv` của dự án**, không cài gì ra hệ thống.

---

## Giai đoạn 0 — Khung repo và ranh giới bảo mật

> Commit: `chore: ignore real data, secrets and virtualenvs` →
> `chore: add env example for MinIO, Airflow and dbt`

Đây là bước **đầu tiên** chứ không phải bước cuối, vì một file `Connections.csv`
thật lỡ commit rồi thì phải rewrite history mới xóa được.

### 0.1 Khởi tạo

```bash
mkdir connection_lens && cd connection_lens
git init
mkdir -p common dags dbt_project streamlit_app/pages services scripts tests docs docker
```

### 0.2 `.gitignore` — viết **trước** khi có file nào khác

Ba nhóm phải chặn tuyệt đối:

```gitignore
# Export LinkedIn thật (PII của chủ sở hữu)
Connections.csv
Connections*.csv
data/
!tests/fixtures/
*.duckdb
*.duckdb.wal

# Volume object storage
minio-data/
volumes/

# Secrets
.env
.env.*
!.env.example

# Config trợ lý AI (§18: không commit)
.claude/
```

Cộng thêm các mục thường lệ: `dbt_project/target/`, `dbt_project/dbt_packages/`,
`dbt_project/logs/`, `great_expectations/uncommitted/`, `.venv/`, `build/`,
`__pycache__/`, `.pytest_cache/`, `.ruff_cache/`.

Lưu ý dòng `!tests/fixtures/`: nó cho phép fixture **tổng hợp** lọt qua rule
`data/`, và sau này CI sẽ dựa đúng vào ngoại lệ này để phân biệt fixture với dữ
liệu thật.

### 0.3 `.env.example` — hợp đồng cấu hình

Không hardcode credential hay path tuyệt đối ở bất kỳ đâu (§17). File này là bản
kê khai đầy đủ mọi biến môi trường, chia theo 6 nhóm:

| Nhóm | Biến chính |
| --- | --- |
| MinIO | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_RAW_PREFIX`, `MINIO_PUBLIC_URL`, `MINIO_ROOT_USER/PASSWORD` |
| DuckDB | `DUCKDB_PATH` |
| Airflow REST | `AIRFLOW_API_BASE_URL`, `AIRFLOW_PUBLIC_URL`, `AIRFLOW_API_USERNAME/PASSWORD`, `AIRFLOW_DAG_ID`, `AIRFLOW_INGESTION_TASK_ID` |
| Airflow runtime | `AIRFLOW_ADMIN_*`, `AIRFLOW_UID`, `AIRFLOW_FERNET_KEY`, `AIRFLOW_JWT_SECRET`, `AIRFLOW_POSTGRES_PASSWORD` |
| Listener | `MINIO_EVENT_LISTENER_TOKEN`, `MINIO_EVENT_LISTENER_PORT` |
| App login + dbt | `STREAMLIT_AUTH_USERNAME/PASSWORD`, `DBT_PROJECT_DIR`, `DBT_PROFILES_DIR`, `DBT_TARGET` |

Hai chi tiết dễ bỏ sót, cả hai đều là bug thật đã gặp:

* **`*_PUBLIC_URL` tách khỏi `*_BASE_URL`.** Trong Docker, Streamlit gọi API tại
  `http://airflow-apiserver:8080`, nhưng trình duyệt trên host không phân giải
  được hostname đó. Link hiển thị trên UI phải trỏ tới `AIRFLOW_PUBLIC_URL`.
* **`STREAMLIT_AUTH_*` không có default.** Chưa cấu hình thì app **không render
  gì cả** — một công cụ ngồi trên PII thật phải fail closed.

---

## Giai đoạn 1 — Quản lý dependency bằng uv

> Commit: `chore(deps): add shared runtime dependencies` →
> `chore(deps): lock every dependency with uv` →
> `infra: generate and drift-check requirements.txt`

### 1.1 Chia dependency theo **group**, không phải một danh sách phẳng

Mỗi runtime chỉ cài đúng thứ nó import. Trong `pyproject.toml`:

```toml
[project]
requires-python = ">=3.12"
dependencies = [        # cái gì cũng cần
    "duckdb>=1.1,<2.0", "pandas>=2.1,<3.0", "minio>=7.2,<8.0",
    "requests>=2.31,<3.0", "pydantic>=2.7,<3.0",
    "pydantic-settings>=2.4,<3.0", "python-dotenv>=1.0,<2.0",
]

[dependency-groups]
app      = ["streamlit>=1.40,<2.0", "altair>=5.4,<6.0"]
listener = ["fastapi>=0.115,<1.0", "uvicorn[standard]>=0.32,<1.0"]
quality  = ["great-expectations>=1.3,<2.0"]
dbt      = ["dbt-core>=1.9,<2.0", "dbt-duckdb>=1.9,<2.0"]
airflow  = ["apache-airflow==3.1.8", "apache-airflow-providers-fab>=2.0"]
dev      = [ {include-group = "app"}, …, "pytest", "sqlfluff", "ruff", … ]

[tool.uv]
package = false
constraint-dependencies = ["cryptography>=42,<43"]
```

Hai dòng cuối là kinh nghiệm xương máu:

* `package = false` — đây là ứng dụng local, không phải thư viện phân phối; uv
  đừng cố build nó.
* Ghim `cryptography<43` — image Airflow ship sẵn provider (google, snowflake)
  kéo theo `pyOpenSSL`, và `pyOpenSSL` vỡ ngay khi `cryptography` vượt lên
  (`module 'lib' has no attribute 'GEN_EMAIL'`).

### 1.2 Lock và export

```bash
uv lock                 # sinh uv.lock
uv sync --frozen        # tạo .venv từ lock, đủ mọi group
```

### 1.3 `requirements.txt` là **file sinh ra**, được commit

Docker image cài từ `requirements.txt` chứ không từ `uv.lock` trực tiếp. Nên file
này được generate, commit, và CI kiểm tra trôi:

```make
EXPORT_GROUPS := --group app --group listener --group quality --group dbt

requirements.txt: pyproject.toml uv.lock
	$(UV) export --frozen --no-emit-project --no-default-groups \
		$(EXPORT_GROUPS) --output-file $@
```

**`apache-airflow` cố tình không nằm trong đó.** Airflow đến từ base image; cài
đè nó từ lock sẽ kéo Flask, Werkzeug và cả stack OpenTelemetry ra khỏi dưới chân
các provider đã có sẵn trong image.

**Kiểm chứng:**

```bash
uv sync --frozen && make requirements-check
```

---

## Giai đoạn 2 — `common/` — lõi dùng chung của hai runtime

> Commit: `feat(common): add shared package for both runtimes` →
> `feat(common): add Airflow REST API client` (11 commit liên tiếp)

**Vì sao có thư mục này thay vì để trong `streamlit_app/`:** MinIO client được
*cả* luồng upload của Streamlit *và* Airflow DAG dùng; Airflow REST client được
*cả* Streamlit *và* event listener dùng. Nhấc chúng ra ngoài giữ một implementation
duy nhất và giữ cả hai unit-testable.

Thứ tự viết đi theo chiều phụ thuộc — module sau import module trước, không có
vòng lặp:

```
errors → hashing → csv_schema → naming → settings → models
       → duckdb_io → minio_client → bronze → data_quality → airflow_client
```

### 2.1 `errors.py` — kiểu lỗi tường minh

Một cây exception gốc là `ConnectionLensError`, với `CsvSchemaError`,
`ObjectKeyError`, `IngestionError`, `LandingZoneError`, `WarehouseNotReadyError`.

Mục đích: **fail loudly** (§17). `LandingZoneError` đặc biệt tồn tại để các
exception của SDK (`minio.error.S3Error`, `urllib3.MaxRetryError`) không bao giờ
lọt tới trang Streamlit — trang chỉ biết xử lý `ConnectionLensError`.

### 2.2 `hashing.py` — khóa idempotency duy nhất

```python
def md5_bytes(data: bytes) -> str: ...
def md5_stream(stream: BinaryIO) -> str: ...   # đọc theo chunk 1 MiB
def md5_file(path) -> str: ...
def short_hash(file_hash, length=8) -> str: ...
```

Idempotency được quyết định bởi **MD5 của bytes file thô, đối chiếu với Bronze** —
không bao giờ theo ngày, theo giờ upload, hay theo trigger nào đã kích DAG.

### 2.3 `csv_schema.py` — dò header động, không bao giờ `skiprows`

Đây là module có nhiều luật nghiệp vụ nhất, vì nó chạm trực tiếp vào định dạng
export thật:

```python
REQUIRED_COLUMNS = ("First Name","Last Name","URL","Company","Position","Connected On")
KNOWN_COLUMNS    = (...)   # 7 cột theo đúng thứ tự export, gồm cả "Email Address"
HEADER_MARKER    = "first name,last name"

def decode_export(raw: bytes) -> tuple[str, str]      # utf-8 → utf-8-sig → latin-1
def detect_header_line_index(text: str) -> int        # quét tìm HEADER_MARKER
def validate_header(columns) -> SchemaValidationResult
def parse_export(raw: bytes) -> ParsedExport          # snake_case cột, giữ nguyên giá trị
```

Bốn quyết định nằm ở đây:

1. **Số dòng note trước header không cố định** giữa các phiên bản export → quét
   tìm dòng bắt đầu bằng `First Name,Last Name`. Hardcode `skiprows=3` là
   anti-pattern dự án này chống lại rõ ràng.
2. **UTF-8 trước tiên** — trường company/position có dấu tiếng Việt.
3. **Cột lạ cũng làm invalid**, không chỉ cột thiếu — schema đổi thì phải nổ, chứ
   không âm thầm ép kiểu hay drop cột.
4. **Giá trị giữ nguyên dạng string**, chỉ snake_case tên cột. Làm sạch, ép kiểu,
   parse ngày là việc của Silver, không phải của ingestion.

### 2.4 `naming.py` — quy ước key vùng landing

```
raw/linkedin_connections/20260823T140501Z_1f3c9ab2.csv
<-------- prefix ------->  <-- snapshot_ts --> <hash8>
```

`build_object_key()` / `parse_object_key()` là cặp đối xứng. Key là **nơi duy
nhất** ghi lại snapshot timestamp, nên Bronze phục hồi được nó về sau. Key không
parse được là lỗi, không phải chỗ để đoán.

### 2.5 `settings.py` — cấu hình từ môi trường

Một class `Settings(BaseSettings)` của pydantic-settings, đọc `.env`, cộng
`@lru_cache` singleton `get_settings()`. Vài property đáng lưu ý:

* `duckdb_file`, `dbt_project_path`, `dbt_profiles_path` — resolve path tương đối
  theo repo root, để chạy từ thư mục nào cũng đúng.
* `has_minio_credentials` / `has_airflow_credentials` / `has_app_credentials` —
  cho phép các lớp trên **fail closed** với thông điệp rõ ràng.

### 2.6 `models.py` — object đi qua ranh giới lớp

Toàn bộ đều là pydantic model round-trip được qua `model_dump(mode="json")`, để
qua được XCom của Airflow mà không cần serializer riêng:

`TriggerSource` (StrEnum: `streamlit` / `minio_event` / `manual_ui`),
`LandingObject`, `ObjectIngestionResult`, `IngestionReport`, `UploadResult`,
`DagRunSummary`, `TaskInstanceSummary`.

### 2.7 `duckdb_io.py` — một writer, nhiều reader

```python
@contextmanager
def connect_read_write(path):   # CHỈ Airflow DAG dùng
@contextmanager
def connect_read_only(path):    # Streamlit luôn dùng cái này
```

Kèm DDL Bronze và các helper: `ensure_bronze`, `bronze_exists`,
`fetch_ingested_hashes`, `hash_in_bronze`, `append_bronze_batch`.

Bảng `bronze.raw_connections` = 7 cột export (snake_case, `varchar`) + 5 cột
metadata: `snapshot_ts`, `file_hash`, `source_object`, `source_row_number`,
`ingested_at`. Append-only: không upsert, không khử trùng lặp người — so sánh
snapshot là việc của Gold.

### 2.8 `minio_client.py` — `LandingZoneClient`

`from_settings()`, `ensure_bucket()`, `check_status()`, `put_export()`,
`list_landing_objects()`, `get_object_bytes()`. Mọi lỗi SDK được bọc thành
`LandingZoneError` bởi context manager `_landing_zone_errors()`.

### 2.9 `bronze.py` — quét → nạp → bỏ qua trùng

Logic ingestion **trigger-agnostic**:

```python
def select_candidate_objects(landing_objects, ingested_hashes)  # lọc rẻ theo hash8
def scan_for_pending_objects(client, connection)
def ingest_object(connection, landing_object, raw)              # kiểm MD5 đầy đủ
def ingest_pending_objects(connection, client, pending)
```

Hai tầng kiểm tra là cố ý: lọc nhanh theo 8 ký tự hash lấy từ tên object, rồi
sau khi tải về mới đối chiếu MD5 đầy đủ với Bronze — **phòng thủ theo chiều sâu,
không tin tầng app**. Nếu hash thật không khớp hash trong tên key, module từ chối
nạp thay vì đoán.

Trùng nội dung thì **log to, rõ, có `md5=`**, không bao giờ skip im lặng (§17).

### 2.10 `data_quality.py` — suite Great Expectations

`build_expectations()` trả về danh sách expectation cho checkpoint Bronze → Silver;
`run_bronze_to_silver_checkpoint(frame)` trả về `DataQualityReport`.

**Không có rule `not_null` trên `email_address`** — LinkedIn chỉ export email khi
người kia đã opt-in, nên đa số dòng trống là đúng thiết kế.

### 2.11 `airflow_client.py` — wrapper REST API

Tách rõ **hàm thuần** (test được không cần mạng) khỏi phần I/O:

* Thuần: `build_api_url`, `build_trigger_payload`, `extract_triggered_by`,
  `parse_dag_run(s)`, `parse_task_instances`, `parse_log_response`.
* I/O: class `AirflowClient` với `trigger_dag_run`, `list_dag_runs`,
  `list_task_instances`, `get_task_log`, `is_healthy`, `set_paused`.

**Airflow 3 dùng `/api/v2` và JWT**, không phải basic auth như Airflow 2: client
lấy token từ `POST /auth/token` rồi cache lại, và retry một lần khi token hết hạn
(cũng chính là cách nó chịu được cold start của apiserver).

**Kiểm chứng giai đoạn 2:** viết test song song ngay (xem [Giai đoạn 8](#giai-đoạn-8--test-suite-và-fixture-tổng-hợp)) —
`test_hashing.py`, `test_csv_schema.py`, `test_naming.py`, `test_bronze_ingestion.py`,
`test_airflow_client.py`, `test_minio_client.py`, `test_data_quality.py`.

---

## Giai đoạn 3 — Airflow DAG ingestion

> Commit: `feat(airflow): add trigger-agnostic ingestion DAG`

File duy nhất: `dags/ingest_connections_dag.py`. Dùng TaskFlow API của
`airflow.sdk` (Airflow 3).

### 3.1 Cấu hình DAG — 4 dòng không được đổi

```python
@dag(
    dag_id="ingest_connections",
    schedule=None,          # không bao giờ cron: chỉ chạy khi người/sự kiện yêu cầu
    catchup=False,
    max_active_runs=1,      # DuckDB single-writer → serialize mọi run
    is_paused_upon_creation=False,
    default_args={"owner": "connection_lens", "retries": 2,
                  "retry_delay": timedelta(minutes=2),
                  "execution_timeout": timedelta(minutes=30)},
    params={"force_transform": Param(False, type="boolean", …)},
)
```

### 3.2 Chuỗi task

| Task | Việc |
| --- | --- |
| `log_trigger_source` | Đọc `dag_run.conf['triggered_by']` **chỉ để ghi log** — không hề ảnh hưởng control flow |
| `scan_landing_zone` | Liệt kê object MinIO có hash chưa nằm trong Bronze |
| `ingest_new_objects_to_bronze` | Nạp cái thật sự mới, bỏ qua trùng kèm log |
| `log_ingestion_summary` | **Nằm ngoài nhánh short-circuit** — run no-op vẫn phải nói rõ nó đã skip gì |
| `has_new_data` (`@task.short_circuit`) | Dừng run khi không có gì mới, trừ khi `force_transform=true` |
| `validate_bronze_batch` | Checkpoint Great Expectations |
| `transform_with_dbt` (task group) | `run` Silver → `snapshot` → `run` marts → `test` → `source freshness` |

### 3.3 Ba chi tiết kỹ thuật

**Import phải sau khi vá `sys.path`.** DAG file nằm trong `dags/`, còn `common/`
ở repo root, nên:

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.bronze import ...   # noqa: E402  ← noqa tường minh, không tắt rule toàn file
```

**Không có việc nặng ở parse time.** `_dbt_command()` đọc settings khi task
*chạy*, không phải khi DAG được parse.

**dbt ghi ra `/tmp`:**

```python
environment = {
    "DUCKDB_PATH": str(settings.duckdb_file),
    "DBT_TARGET_PATH": "/tmp/dbt_target",
    "DBT_LOG_PATH":    "/tmp/dbt_logs",
}
```

để dbt không cần quyền ghi vào thư mục project được bind-mount.

**Kiểm chứng:**

```bash
make dag-check   # parse DAG bằng Airflow thật + assert max_active_runs/schedule/catchup
```

---

## Giai đoạn 4 — dbt: Silver → Gold → marts

> Commit: `feat(dbt): configure medallion layers and schemas` →
> `test(dbt): warn on snapshot volume anomalies` (18 commit)

### 4.1 `dbt_project.yml` — schema chính là tên tầng

```yaml
models:
  connection_lens:
    staging:  {+schema: silver, +materialized: table}
    marts:
      +schema: gold
      +materialized: table
      mart_network_stats:     {+schema: mart}
      mart_network_breakdown: {+schema: mart}
snapshots:
  connection_lens: {+target_schema: gold}
vars:
  min_identifiable_share: 0.90
```

Kèm macro `generate_schema_name.sql` override mặc định của dbt, để schema đọc ra
đúng `silver` / `gold` / `mart` thay vì `<target>_<custom>`.

### 4.2 `profiles.yml` — `DUCKDB_PATH` **bắt buộc, không default**

```yaml
connection_lens:
  target: "{{ env_var('DBT_TARGET', 'dev') }}"
  outputs:
    dev: {type: duckdb, path: "{{ env_var('DUCKDB_PATH') }}", threads: 4}
    ci:  {type: duckdb, path: "{{ env_var('DUCKDB_PATH') }}", threads: 2}
```

Cố tình không có giá trị mặc định: hardcode đường dẫn warehouse là anti-pattern
ở đây, và fail to còn hơn âm thầm tạo một file warehouse thứ hai ở chỗ nào đó.

### 4.3 Packages

```bash
uv run dbt deps --project-dir dbt_project    # dbt_utils + dbt_expectations
```

Cả hai đều là package **chỉ chứa macro**, không materialize model nào.

### 4.4 `macros/duckdb_dialect.sql` — cô lập SQL riêng của DuckDB

`parse_linkedin_date` (`try_strptime(..., '%d %b %Y')`), `date_key`,
`date_spine`, `normalise_company`. Đổi adapter về sau là viết lại macro, không
phải viết lại model.

### 4.5 Thứ tự xây model

| # | File | Grain | Điểm mấu chốt |
| --- | --- | --- | --- |
| 1 | `models/staging/_bronze__sources.yml` | — | Khai báo source + `freshness` (cảnh báo sau 30 ngày) |
| 2 | `models/staging/stg_connections.sql` | 1 dòng / connection / `snapshot_ts` | Làm sạch, ép kiểu, cờ `is_identifiable`, sinh `company_key` |
| 3 | `snapshots/dim_connection_snapshot.sql` | 1 version SCD2 / connection | **Xem 4.6** |
| 4 | `models/marts/dim_company.sql` | 1 dòng / employer | Không lộ công ty → gom vào `(unknown)`, không drop |
| 5 | `models/marts/dim_date.sql` | 1 dòng / ngày | Spine sinh từ chính dữ liệu |
| 6 | `models/marts/fct_connection_snapshot.sql` | `connection_id` + `snapshot_ts` | Người biến mất đơn giản là **không có dòng** ở snapshot đó |
| 7 | `models/marts/mart_network_stats.sql` | 1 dòng / snapshot | Số học growth/churn làm sẵn để Streamlit chỉ việc vẽ |
| 8 | `models/marts/mart_network_breakdown.sql` | snapshot + dimension + value | Dạng tidy: thêm một breakdown = thêm một nhánh UNION |

Không dùng `SELECT *` ở bất kỳ model nào.

### 4.6 Snapshot SCD2 — hai config không được đụng vào

```sql
{{ config(
    target_schema='gold', unique_key='connection_id',
    strategy='check', check_cols=['company', 'position'],
    hard_deletes='invalidate'
) }}
```

* **`hard_deletes='invalidate'`** — không có nó, người vắng mặt ở export mới nhất
  sẽ mãi mãi `is_current=true`, và câu hỏi "ai đã rời network" thành không trả
  lời được. **Không suy diễn hay lưu *lý do* biến mất** — LinkedIn không cung cấp
  tín hiệu nào cả, đoán bừa là đặt một sự thật bịa đặt trước một quyết định
  outreach thật.
* **Input lọc về đúng snapshot mới nhất:**

  ```sql
  inner join latest_snapshot on stg.snapshot_ts = latest_snapshot.max_snapshot_ts
  where stg.is_identifiable
  ```

  `strategy='check'` diff input với trạng thái snapshot hiện tại; cho ăn toàn bộ
  lịch sử Silver sẽ phá vỡ logic diff.

### 4.7 Test — bắt buộc, không thương lượng

* Generic: `unique` + `not_null` trên mọi key, `relationships` fact→dim,
  `accepted_values` cho trường phân loại.
* Singular (`dbt_project/tests/`):
  * `assert_one_current_row_per_connection.sql`
  * `assert_current_fact_rows_have_dim_connection.sql`
  * `assert_one_bronze_batch_per_snapshot_ts.sql`
  * `assert_identifiable_share_within_threshold.sql` (ngưỡng `min_identifiable_share`)
  * `assert_snapshot_volume_is_not_anomalous.sql`

**Không có test `not_null` trên `email_address`** ở bất kỳ đâu.

**Kiểm chứng:** cần một warehouse để build — dựng nó ở
[Giai đoạn 8](#giai-đoạn-8--test-suite-và-fixture-tổng-hợp) rồi `make ci-warehouse`.

---

## Giai đoạn 5 — Great Expectations checkpoint

> Commit: `feat(quality): add Bronze to Silver checkpoint CLI`

Suite thật sự sống trong `common/data_quality.py` (đã viết ở giai đoạn 2);
`great_expectations/checkpoints/bronze_to_silver.py` chỉ là CLI mỏng để chạy tay
cùng bộ kiểm tra mà DAG chạy.

Checkpoint kiểm: tập cột **chính xác**, toàn vẹn metadata, hợp đồng định dạng URL
và ngày. Nó **đếm** dòng restricted profile chứ không loại bỏ chúng.

---

## Giai đoạn 6 — Streamlit: 4 tab

> Commit: `feat(streamlit): add serving layer package` →
> `feat(streamlit): add job management tab` (10 commit)

Thứ tự viết: logic thuần trước, trang giao diện sau — để mọi thứ đáng test đều
nằm ngoài file page.

### 6.1 Logic thuần (test bằng pytest, không cần Streamlit)

| File | Nội dung |
| --- | --- |
| `tagging.py` | `tag_connection(position) -> list[str]` — 6 tag `recruiter_talent` / `leadership` / `executive` / `target_peer` / `engineering` / `early_career`, **không loại trừ nhau** |
| `scoring.py` | `ReferralWeights` (dataclass), `score_referral()` — mỗi điểm số mang theo **lý do**, nên chỉnh trọng số là sửa một dòng |
| `upload_service.py` | `prepare_upload()` → `perform_upload()` — validate → hash → kiểm Bronze → land vào MinIO |
| `db.py` | Mọi truy vấn, luôn qua `connect_read_only`; `safe_query()` trả DataFrame rỗng khi warehouse chưa có |

Thứ tự trong `upload_service` là hợp đồng, không phải tùy tiện:

1. Layer-1 validation **trước** — file thiếu cột bắt buộc không bao giờ được hash
   hay upload;
2. MD5 của bytes thô là khóa idempotency, tính client-side;
3. hash đối chiếu với **Bronze** (dataset of record), không phải MinIO — MinIO cố
   ý giữ trùng lặp làm audit trail;
4. file được upload **trong cả hai trường hợp**; bản trùng chỉ nhận thông điệp rõ
   ràng rằng sẽ không có dataset mới nào được tạo.

Tab Upload **không bao giờ** kích ingestion — việc đó thuộc về Job Management.

### 6.2 Hạ tầng trình bày

`charts.py` (palette đã kiểm chứng + các chart Altair), `ui.py` (`configure_page`,
`display_profile_url` — `unquote()` URL chỉ để hiển thị, `format_timestamp`,
`minio_status`), `auth.py` (`require_login()` — chặn cả 4 tab, fail closed khi
`STREAMLIT_AUTH_*` chưa cấu hình).

### 6.3 Bốn trang

| File | Vai trò |
| --- | --- |
| `pages/1_upload.py` | Validate + hash + đẩy lên MinIO. Không trigger. |
| `pages/2_network_stats.py` | Chart read-only từ `mart_network_stats` / `mart_network_breakdown` |
| `pages/3_job_search.py` | Bảng lọc/sắp xếp toàn bộ connection hiện tại, xếp hạng theo **referral strength** (không cần nhập gì) |
| `pages/4_job_management.py` | Nút trigger + bảng lịch sử run + viewer log, qua Airflow REST |

`app.py` là entrypoint: vá `sys.path`, `require_login()`, hiển thị trạng thái ba
thành phần (warehouse / MinIO / Airflow).

**Kiểm chứng:** `make app` → mở http://localhost:8501.

---

## Giai đoạn 7 — FastAPI event listener (trigger mode 2)

> Commit: `feat(services): translate bucket events to DAG runs`

`services/minio_event_listener/main.py` — một service dịch:

```
MinIO --s3:ObjectCreated:*--> listener --REST(JWT)--> Airflow DAG run
```

**Vì sao cần service riêng:** payload webhook của MinIO không có hình dạng mà API
Airflow chờ đợi, và MinIO không gắn được header auth của Airflow vào đó.

Hai endpoint: `GET /health` (kèm cả `airflow_reachable`) và
`POST /minio/events`. Bảo vệ bằng bearer token chia sẻ
(`MINIO_EVENT_LISTENER_TOKEN`); để trống thì bỏ qua kiểm tra (tiện lợi local).

`extract_created_objects()` là hàm thuần: bỏ qua mọi event không phải
ObjectCreated và mọi key ngoài prefix landing zone, `unquote_plus` key vì MinIO
percent-encode nó.

Run được tag `triggered_by="minio_event"` **chỉ để tab Job Management quy trách
nhiệm được**; object key gửi kèm là **metadata thuần** — DAG không bao giờ đọc
nó, nó luôn quét lại toàn bộ landing zone.

---

## Giai đoạn 8 — Test suite và fixture tổng hợp

> Commit: `feat(scripts): build a CI warehouse from fixtures` →
> `test: smoke test every Streamlit page` (17 commit)

### 8.1 Fixture — dữ liệu tổng hợp, thiết kế có chủ đích

`tests/fixtures/connections_v1.csv` và `connections_v2.csv` khác nhau ở đúng
những thứ cần chứng minh:

* một người **đổi công ty**,
* một người **đổi chức danh**,
* một người **biến mất**,
* hai người **mới**,
* và — quan trọng — **số dòng note trước header khác nhau** (3 và 4), nên logic
  dò header động bị thử thách ở mọi lần chạy CI.

Cộng thêm `connections_missing_column.csv` cho kịch bản validation thất bại, và
một dòng restricted profile (chỉ có ngày, mọi trường khác trống).

### 8.2 Scripts

* `scripts/seed_ci_warehouse.py` — dựng warehouse từ fixture (`--overwrite` /
  `--append`), thay cho việc phải chạy MinIO trong CI.
* `scripts/assert_scd2_behaviour.py` — assert end-to-end rằng lịch sử SCD2 đúng
  như spec mô tả sau khi nạp hai snapshot.

### 8.3 Bao phủ test

| File | Bao phủ |
| --- | --- |
| `test_hashing.py` | MD5 bytes/stream/file, short hash |
| `test_csv_schema.py` | Dò header, decode, validate, parse |
| `test_naming.py` | Build/parse object key, key hỏng thì nổ |
| `test_bronze_ingestion.py` | **11 kịch bản idempotency của §14** |
| `test_data_quality.py` | Suite expectation |
| `test_tagging.py`, `test_scoring.py` | Taxonomy + trọng số |
| `test_airflow_client.py` | Request/response (mock), JWT, retry cold-start |
| `test_minio_client.py` | Landing zone client |
| `test_upload_service.py` | Luồng upload với fake client |
| `test_event_listener.py` | Listener qua `TestClient` |
| `test_streamlit_pages.py` | Smoke test cả 4 trang |

`tests/conftest.py` cung cấp fake landing-zone client — không test nào cần MinIO
thật.

**Kiểm chứng:**

```bash
make ci-warehouse    # seed v1 → dbt build → seed v2 → dbt build
make test
```

---

## Giai đoạn 9 — Docker hóa toàn bộ stack

> Commit: `infra(docker): add Airflow image with a dbt venv` →
> `infra: add make targets for setup and checks`

### 9.1 Ba Dockerfile, cùng một `requirements.txt`

| Image | Base | Ghi chú |
| --- | --- | --- |
| `docker/airflow/Dockerfile` | `apache/airflow:3.1.8-python3.12` | `pip install --user -r requirements.txt`; **không** cài lại `apache-airflow` |
| `docker/streamlit/Dockerfile` | `python:3.12-slim` | Chỉ đọc warehouse |
| `docker/listener/Dockerfile` | `python:3.12-slim` | `uvicorn services.minio_event_listener.main:app` |

Trên Airflow 2, dbt và Airflow **không thể** chung một interpreter (constraints
của Airflow + `dbt-core` = `ResolutionImpossible`), nên image từng phải mang hai
virtualenv. Trên Airflow 3 chúng dùng chung một interpreter.

### 9.2 `docker-compose.yml`

Dùng YAML anchor để không lặp cấu hình: `x-minio-settings`, `x-airflow-common`,
`x-app-environment`.

| Service | Vai trò |
| --- | --- |
| `minio` | Landing zone, cổng 9000 (S3) + 9001 (console) |
| `minio-init` | One-shot: tạo bucket, bật versioning |
| `airflow-postgres` | Metadata DB |
| `airflow-init` | Migrate DB + tạo admin user |
| `airflow-scheduler` | LocalExecutor |
| `airflow-apiserver` | Airflow 3 tách webserver cũ thành API server, cổng 8080 |
| `airflow-dag-processor` | Airflow 3 yêu cầu chạy như service riêng |
| `streamlit` | Cổng 8501 |
| `minio-event-listener` | Cổng 8000 |

Bốn điểm dễ vấp:

* **`user: "${AIRFLOW_UID:-50000}:0"`** — container Airflow phải chạy dưới UID của
  host, nếu không file DuckDB bind-mount sẽ không ghi được. `make env` tự pin
  `AIRFLOW_UID` theo `id -u`.
* **Warehouse mount read-only cho Streamlit** (`./data:...:ro`) — Streamlit không
  thể ghi vào Bronze/Gold, dù code có làm gì đi nữa.
* **`minio` `depends_on` listener healthy** — MinIO kiểm tra endpoint webhook
  ngay khi khởi động, nên listener phải trả lời được trước.
* **`AIRFLOW__CORE__AUTH_MANAGER: …FabAuthManager`** — FAB cung cấp endpoint
  `/auth/token` mà Streamlit và listener dùng để lấy JWT.

### 9.3 `Makefile` — entry point cho dev

```
make venv          # uv sync --frozen
make dbt-deps      # dbt deps
make env           # tạo .env + pin AIRFLOW_UID
make up / down / ps / logs
make test / lint / format
make ci-warehouse  # warehouse từ fixture
make dag-check     # parse DAG bằng Airflow thật
make check         # tất cả những gì CI chạy, chạy tại máy
make app           # Streamlit từ .venv, không cần Docker
make minio-events  # đăng ký bucket notification (trigger mode 2)
```

**Kiểm chứng:**

```bash
make env && $EDITOR .env && make up && make ps
```

---

## Giai đoạn 10 — CI trên GitHub Actions

> Commit: `infra(ci): lint, test and build on every PR` →
> `infra(ci): fail on a stale requirements.txt`

Không có bước deploy — đây là công cụ local-only. CI tồn tại để giữ chất lượng và
để **chứng minh không có dữ liệu cá nhân thật nào lọt vào repo**.

Ba job:

**1. `privacy-guard`** — chạy trước mọi thứ:

```bash
git ls-files | grep -Ei '(^|/)connections.*\.csv$|\.duckdb$|(^|/)\.env$|(^|/)minio-data/|(^|/)\.claude/' \
  | grep -v '^tests/fixtures/'
```

Có kết quả → fail.

**2. `quality`** — theo đúng thứ tự:

`uv sync --frozen` → `make requirements-check` → `dbt deps` → `ruff` →
seed warehouse v1 → `sqlfluff lint` → `dbt build` → seed v2 → `dbt build` →
`assert_scd2_behaviour.py` → `pytest --cov`.

`--frozen` là cố ý: lock lệch pha với `pyproject.toml` thì fail, chứ không âm
thầm resolve ra thứ khác với môi trường dev.

**3. `dag-parse`** — cài `--no-default-groups --group airflow`, import DagBag,
assert `max_active_runs == 1`, `schedule is None`, `catchup is False`.

---

## Giai đoạn 11 — Tài liệu

> Commit: `docs: add project README` → `docs: document the data quality rules`

| File | Nội dung |
| --- | --- |
| `README.md` | Cái gì / kiến trúc / quickstart / data model / test & CI |
| `docs/architecture_decisions.md` | ADR ngắn: mỗi lựa chọn **loại trừ** điều gì |
| `docs/data_quality.md` | Luật rút ra từ export thật, tái hiện trong fixture |
| `docs/build_guide.md` | Chính tài liệu này |

---

## Giai đoạn 12 — Chạy thử end-to-end

```bash
make venv && make dbt-deps
make env && $EDITOR .env          # điền credential MinIO + Airflow + app login
make up
```

| Service | URL |
| --- | --- |
| Streamlit | http://localhost:8501 |
| Airflow | http://localhost:8080 |
| MinIO console | http://localhost:9001 |

Rồi trong app:

1. **Upload** — thả `Connections.csv`. File được validate và hash **trước khi**
   bất cứ thứ gì được upload; nó đáp xuống MinIO. Tab này không bắt đầu ingestion.
2. **Job Management** — bấm *Trigger ingestion now*, xem run, nguồn trigger và log
   ngay trong app.
3. **Network Stats** / **Job Search** — đọc kết quả.

Bật trigger mode 2 (tùy chọn): đặt `MINIO_WEBHOOK_ENABLE=on` trong `.env`,
`make up` lại, rồi `make minio-events`.

### Checklist nghiệm thu — 11 kịch bản của §14

| # | Kịch bản | Kỳ vọng |
| --- | --- | --- |
| 1 | Upload lần đầu | Vào MinIO + Bronze, ai cũng có dòng `dim_connection` |
| 2 | Upload lại **đúng file cũ** | Vẫn lên MinIO (audit trail), **skip** ở Bronze kèm log to |
| 3 | Nội dung khác, cùng ngày | Cả hai đều được nạp — ngày lịch không liên quan |
| 4 | Một người đổi công ty | Dòng SCD2 cũ đóng lại, dòng current mới mở ra |
| 5 | Một người biến mất | `dbt_valid_to` được set, hết `is_current`. **Không lưu lý do** |
| 6 | Export thiếu cột | Bị chặn ngay trên trình duyệt, trước cả hash lẫn upload |
| 7 | Trigger hai lần liên tiếp | Lần hai không tìm thấy gì mới → no-op |
| 8 | Bucket event trên object mới | Nạp bình thường, tag `triggered_by=minio_event` |
| 9 | Trigger tay khi không có gì pending | No-op |
| 10 | Hai nguồn trigger cùng lúc | `max_active_runs=1` serialize, run sau là no-op |
| 11 | Mở tab Job Management | Lịch sử run + log hiển thị qua REST |

Chạy `make check` để xác nhận toàn bộ trước khi mở PR.

---

## Bẫy đã gặp — đọc trước khi debug

| Triệu chứng | Nguyên nhân | Cách xử lý |
| --- | --- | --- |
| `module 'lib' has no attribute 'GEN_EMAIL'` | `cryptography` vượt lên trước `pyOpenSSL` trong image Airflow | Giữ `constraint-dependencies = ["cryptography>=42,<43"]` |
| Image build ra khác môi trường dev | `requirements.txt` trôi khỏi `uv.lock` | `make requirements`; CI đã chặn sẵn |
| DAG import lỗi trong container | `PYTHONPATH` thiếu, hoặc import đặt trước khi vá `sys.path` | `PYTHONPATH=/opt/connection_lens` + `# noqa: E402` đúng chỗ |
| DuckDB `Permission denied` khi DAG ghi | Container chạy UID khác chủ file bind-mount | `make env` pin `AIRFLOW_UID=$(id -u)` |
| Link Airflow trên UI không mở được | UI đang hiển thị URL nội bộ Docker | Dùng `AIRFLOW_PUBLIC_URL` / `MINIO_PUBLIC_URL` cho mọi link render |
| `dbt` báo thiếu `DUCKDB_PATH` | Cố ý — không có default | Set biến, hoặc dùng `make dbt-build` |
| MinIO không khởi động khi bật webhook | MinIO validate endpoint lúc boot | Listener phải healthy trước; đã khai `depends_on` |
| Airflow REST trả 401 | Airflow 3 dùng JWT `/auth/token`, không phải basic auth | `AirflowClient` đã xử lý; kiểm `AIRFLOW_JWT_SECRET` |
| dbt không ghi được `target/` | Thư mục project bind-mount | Đã redirect `DBT_TARGET_PATH` / `DBT_LOG_PATH` sang `/tmp` |

---

## Quy ước commit

Theo `.claude/rules/git_commit_convention.md`:

```
<type>(<scope>): <tóm tắt ở thể mệnh lệnh>
```

`feat` · `fix` · `docs` · `style` · `refactor` · `perf` · `test` · `chore` · `infra`.
Tối đa 50 ký tự (không quá 72), không dấu chấm cuối, **atomic — một file một commit**.
Lịch sử commit của dự án chính là bản thu nhỏ của tài liệu này.
