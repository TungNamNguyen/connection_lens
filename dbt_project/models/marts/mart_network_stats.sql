{{ config(materialized='table', tags=['mart']) }}

/*
    Data mart powering the Network Stats tab: one row per ingested snapshot,
    with the growth/churn arithmetic already done so Streamlit only has to
    plot it (§9).
*/

with fct as (

    select
        connection_id,
        snapshot_ts,
        snapshot_date_key,
        snapshot_sequence,
        previous_snapshot_ts,
        company_key,
        has_email,
        is_new_since_previous_snapshot
    from {{ ref('fct_connection_snapshot') }}

),

per_snapshot as (

    select
        snapshot_ts,
        min(snapshot_date_key) as snapshot_date_key,
        min(snapshot_sequence) as snapshot_sequence,
        min(previous_snapshot_ts) as previous_snapshot_ts,
        cast(count(*) as integer) as total_connections,
        cast(count(distinct company_key) as integer) as distinct_companies,
        cast(sum(case when has_email then 1 else 0 end) as integer)
            as connections_with_email,
        cast(sum(case when is_new_since_previous_snapshot then 1 else 0 end) as integer)
            as new_connections
    from fct
    group by snapshot_ts

),

lost as (

    /* Anti-join: connections present in the previous snapshot and absent from
       this one. The fact of disappearance only — never a reason (§5, §14). */
    select
        per_snapshot.snapshot_ts,
        cast(count(*) as integer) as lost_connections
    from per_snapshot
    inner join fct as previous_fct
        on per_snapshot.previous_snapshot_ts = previous_fct.snapshot_ts
    left join fct as current_fct
        on
            per_snapshot.snapshot_ts = current_fct.snapshot_ts
            and previous_fct.connection_id = current_fct.connection_id
    where current_fct.connection_id is null
    group by per_snapshot.snapshot_ts

),

restricted as (

    select
        snapshot_ts,
        cast(sum(case when is_identifiable then 0 else 1 end) as integer)
            as restricted_profile_rows,
        cast(sum(case when company is null then 1 else 0 end) as integer)
            as connections_without_company,
        cast(sum(case when position is null then 1 else 0 end) as integer)
            as connections_without_position
    from {{ ref('stg_connections') }}
    group by snapshot_ts

)

select
    per_snapshot.snapshot_ts,
    per_snapshot.snapshot_date_key,
    per_snapshot.snapshot_sequence,
    per_snapshot.previous_snapshot_ts,
    per_snapshot.total_connections,
    per_snapshot.new_connections,
    coalesce(lost.lost_connections, 0) as lost_connections,
    cast(per_snapshot.new_connections - coalesce(lost.lost_connections, 0) as integer)
        as net_change,
    per_snapshot.distinct_companies,
    per_snapshot.connections_with_email,
    round(
        100.0 * per_snapshot.connections_with_email
        / nullif(per_snapshot.total_connections, 0),
        2
    ) as email_coverage_pct,
    coalesce(restricted.restricted_profile_rows, 0) as restricted_profile_rows,
    coalesce(restricted.connections_without_company, 0) as connections_without_company,
    coalesce(restricted.connections_without_position, 0) as connections_without_position
from per_snapshot
left join lost on per_snapshot.snapshot_ts = lost.snapshot_ts
left join restricted on per_snapshot.snapshot_ts = restricted.snapshot_ts
