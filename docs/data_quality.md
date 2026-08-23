# Data quality

Every rule here comes from looking at a **real** LinkedIn export, then being
reproduced in the synthetic fixtures so it is exercised on every CI run.

## What a real export actually contains

| Observation | Consequence in the pipeline |
| --- | --- |
| The note block before the header is **not a fixed number of lines** across export versions. | The header row is located by scanning for `First Name,Last Name`. A hardcoded `skiprows` is forbidden — the two CI fixtures deliberately have a different number of note lines (3 and 4). |
| Most rows have **no email address** (LinkedIn only exports it when the connection opted in). | There is no `not_null` test or expectation on `email_address`, anywhere. |
| Some rows carry **only a connection date** — no URL, no name, no company, no title. These are restricted or deactivated profiles. | They cannot be given a stable identity, so they are flagged, excluded from Gold, and **counted**. See below. |
| Company and position fields contain **Vietnamese diacritics** and punctuation. | Files are read as UTF-8 (with a `utf-8-sig` fallback), and company matching normalises unicode rather than stripping it. |
| Names and titles contain **commas inside quoted values** (`"Hoa, Ph.D"`, `"Senior Analytics Engineer, Tech Lead"`). | Standard CSV quoting handles this; no custom parsing. |
| Profile URLs are **percent-encoded**. | `connection_id` keeps the raw encoded string; it is only `unquote`d for display. |

## Restricted profiles

A restricted profile row looks like this — everything blank except the date:

```csv
First Name,Last Name,URL,Email Address,Company,Position,Connected On
,,,,,,12 Nov 2025
```

Dropping these rows silently would quietly change every count. Instead:

1. **Silver** keeps them and sets `is_identifiable = false`.
2. **Gold** filters on `is_identifiable`, because SCD2 needs a stable key and
   the fact table's grain is `connection_id + snapshot_ts`.
3. **`mart_network_stats.restricted_profile_rows`** counts them per snapshot,
   and the Network Stats tab shows the number.
4. A dbt test fails the build if the identifiable share of any snapshot drops
   below `min_identifiable_share` (90%), which is what a genuine export-format
   regression would look like.

## The Bronze → Silver checkpoint

`common/data_quality.py` defines the Great Expectations suite; the Airflow DAG
runs it after ingestion and before dbt, and
`great_expectations/checkpoints/bronze_to_silver.py` runs the same suite from
the command line.

| Expectation | Why |
| --- | --- |
| `ExpectTableColumnsToMatchSet(exact_match=True)` | A schema change must fail loudly, never be silently coerced. |
| `ExpectTableRowCountToBeBetween(min_value=1)` | An empty batch is a bug, not a snapshot. |
| `ExpectColumnValuesToNotBeNull` on `file_hash`, `snapshot_ts`, `source_object`, `ingested_at` | Ingestion metadata is what makes idempotency and lineage work. |
| `ExpectColumnValueLengthsToEqual(file_hash, 32)` | The idempotency key must be a full MD5. |
| `ExpectColumnValuesToMatchRegex(url, …, mostly=0.90)` | Most rows must carry a real profile URL; a few restricted profiles are normal, half the file is not. |
| `ExpectColumnValuesToMatchRegex(connected_on, '^\d{1,2} [A-Za-z]{3} \d{4}$')` | The export's date format is part of the contract; if LinkedIn changes it, stop rather than parse garbage. |
| *(deliberately absent)* `email_address` not-null | Blank is correct, not a defect. |

## dbt tests

| Test | Layer | Severity |
| --- | --- | --- |
| `unique` + `not_null` on every key (`company_key`, `date_key`, `dbt_scd_id`, `connection_snapshot_key`, `snapshot_ts`) | Silver, Gold, marts | error |
| `unique_combination_of_columns` on the fact and Silver grains | Silver, Gold | error |
| `relationships` from the fact to `dim_company` and `dim_date` | Gold | error |
| `assert_current_fact_rows_have_dim_connection` | Gold | error |
| `assert_one_current_row_per_connection` — SCD2 can have many versions but only one open row | Gold | error |
| `assert_one_bronze_batch_per_snapshot_ts` — two file hashes under one snapshot means idempotency broke | Silver | error |
| `assert_identifiable_share_within_threshold` | Silver | error |
| `assert_snapshot_volume_is_not_anomalous` — a >50% swing between snapshots | Gold | warn |
| `expect_column_values_to_be_between` on `connected_on` | Silver | warn |
| `dbt source freshness` on `bronze.raw_connections` | Bronze | warn at 30d, error at 90d |

The volume and freshness checks are what Elementary OSS would otherwise
provide; wiring Elementary in remains an open item.
