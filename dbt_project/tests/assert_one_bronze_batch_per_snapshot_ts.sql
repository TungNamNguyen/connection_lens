{#-
    Bronze is append-only and each ingested file gets its own snapshot_ts, so
    a snapshot_ts carrying two different file hashes means idempotency broke.
-#}
select
    snapshot_ts,
    count(distinct file_hash) as file_hash_count
from {{ ref('stg_connections') }}
group by snapshot_ts
having count(distinct file_hash) > 1
