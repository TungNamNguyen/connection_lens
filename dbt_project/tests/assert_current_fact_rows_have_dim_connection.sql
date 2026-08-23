{#-
    Referential integrity between the fact and the SCD2 dimension, scoped to
    the current snapshot. Historical fact rows for people who left before the
    first snapshot run have no dimension row by design, so an unscoped
    relationships test would flag a correct warehouse.
-#}
with latest as (

    select max(snapshot_ts) as max_snapshot_ts
    from {{ ref('fct_connection_snapshot') }}

)

select fct.connection_id
from {{ ref('fct_connection_snapshot') }} as fct
inner join latest on fct.snapshot_ts = latest.max_snapshot_ts
left join {{ ref('dim_connection') }} as dim
    on fct.connection_id = dim.connection_id
where dim.connection_id is null
