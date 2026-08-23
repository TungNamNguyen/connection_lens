{{ config(materialized='table', tags=['gold']) }}

/*
    Gold fact: one row per connection per ingested snapshot (§5).

    SCD2 only knows about the *current* run; growth and churn charts need the
    full historical picture, which is what this table provides. A connection
    that disappeared simply has no row at that snapshot_ts — no flag, and no
    inferred reason (§5, §14 scenario 5).
*/

with silver as (

    select
        connection_id,
        company_key,
        company_label,
        position,
        connected_on,
        connected_on_date_key,
        snapshot_ts,
        snapshot_date,
        snapshot_date_key,
        has_email,
        file_hash
    from {{ ref('stg_connections') }}
    where is_identifiable

),

snapshot_sequence as (

    select
        snapshot_ts,
        row_number() over (order by snapshot_ts) as snapshot_sequence,
        lag(snapshot_ts) over (order by snapshot_ts) as previous_snapshot_ts
    from (select distinct snapshot_ts from silver) as distinct_snapshots

),

first_appearance as (

    select
        connection_id,
        min(snapshot_ts) as first_seen_snapshot_ts
    from silver
    group by connection_id

)

select
    {{ dbt_utils.generate_surrogate_key(['silver.connection_id', 'silver.snapshot_ts']) }}
        as connection_snapshot_key,
    silver.connection_id,
    silver.snapshot_ts,
    silver.snapshot_date,
    silver.snapshot_date_key,
    snapshot_sequence.snapshot_sequence,
    snapshot_sequence.previous_snapshot_ts,
    silver.company_key,
    silver.company_label,
    silver.position,
    silver.connected_on,
    silver.connected_on_date_key,
    silver.has_email,
    silver.file_hash,
    silver.snapshot_ts = first_appearance.first_seen_snapshot_ts as is_first_appearance,
    previous_presence.connection_id is null
    and snapshot_sequence.previous_snapshot_ts is not null
        as is_new_since_previous_snapshot,
    cast(
        date_diff('day', silver.connected_on, silver.snapshot_date) as integer
    ) as days_connected_at_snapshot
from silver
inner join snapshot_sequence
    on silver.snapshot_ts = snapshot_sequence.snapshot_ts
inner join first_appearance
    on silver.connection_id = first_appearance.connection_id
left join silver as previous_presence
    on
        silver.connection_id = previous_presence.connection_id
        and snapshot_sequence.previous_snapshot_ts = previous_presence.snapshot_ts
