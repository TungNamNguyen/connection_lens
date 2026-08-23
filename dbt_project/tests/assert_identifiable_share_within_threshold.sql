{#-
    Restricted profiles (blank URL) are expected and tolerated, but a sudden
    collapse in identifiable rows means the export changed shape — surface it
    instead of quietly modelling 3 people (see docs/data_quality.md).
-#}
{{ config(severity='error') }}

with per_snapshot as (

    select
        snapshot_ts,
        count(*) as total_rows,
        sum(case when is_identifiable then 1 else 0 end) as identifiable_rows
    from {{ ref('stg_connections') }}
    group by snapshot_ts

)

select
    snapshot_ts,
    total_rows,
    identifiable_rows,
    cast(identifiable_rows as double) / nullif(total_rows, 0) as identifiable_share
from per_snapshot
where
    cast(identifiable_rows as double) / nullif(total_rows, 0)
    < {{ var('min_identifiable_share') }}
