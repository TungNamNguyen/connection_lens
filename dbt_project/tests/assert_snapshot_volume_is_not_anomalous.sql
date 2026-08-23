{#-
    Volume anomaly detection (§12): a new export that swings by more than 50%
    against the previous snapshot is almost certainly a partial or corrupted
    export rather than real network churn. Warn rather than fail — a genuine
    bulk cleanup is possible.
-#}
{{ config(severity='warn') }}

with per_snapshot as (

    select
        snapshot_ts,
        count(*) as connection_count
    from {{ ref('fct_connection_snapshot') }}
    group by snapshot_ts

),

with_previous as (

    select
        snapshot_ts,
        connection_count,
        lag(connection_count) over (order by snapshot_ts) as previous_count
    from per_snapshot

)

select
    snapshot_ts,
    connection_count,
    previous_count,
    abs(connection_count - previous_count) / nullif(cast(previous_count as double), 0)
        as relative_change
from with_previous
where
    previous_count is not null
    and abs(connection_count - previous_count)
    / nullif(cast(previous_count as double), 0) > 0.5
