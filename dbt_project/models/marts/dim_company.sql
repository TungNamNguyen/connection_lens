{{ config(materialized='table', tags=['gold']) }}

/*
    Gold dimension: one row per distinct employer seen anywhere in the
    network's history. Companies are matched on a whitespace-squeezed,
    lower-cased name; connections that did not disclose an employer roll up
    into a single '(unknown)' member rather than being dropped.

    No industry/size attributes here — enrichment is still an open decision
    (§15) and this project never scrapes LinkedIn for them.
*/

with silver as (

    select
        company_key,
        company_label,
        company_normalised,
        connection_id,
        connected_on,
        snapshot_ts
    from {{ ref('stg_connections') }}
    where is_identifiable

),

latest_label as (

    select
        company_key,
        company_label,
        row_number() over (
            partition by company_key
            order by snapshot_ts desc, company_label asc
        ) as label_recency
    from silver

),

aggregated as (

    select
        company_key,
        min(company_normalised) as company_normalised,
        min(snapshot_ts) as first_seen_snapshot_ts,
        max(snapshot_ts) as last_seen_snapshot_ts,
        min(connected_on) as first_connected_on,
        max(connected_on) as last_connected_on,
        count(distinct connection_id) as connections_ever
    from silver
    group by company_key

),

current_connections as (

    select
        company_key,
        count(*) as current_connection_count
    from {{ ref('dim_connection') }}
    where dbt_valid_to is null
    group by company_key

)

select
    aggregated.company_key,
    latest_label.company_label as company_name,
    aggregated.company_normalised,
    latest_label.company_label = '(unknown)' as is_unknown_company,
    aggregated.first_seen_snapshot_ts,
    aggregated.last_seen_snapshot_ts,
    aggregated.first_connected_on,
    aggregated.last_connected_on,
    aggregated.connections_ever,
    coalesce(current_connections.current_connection_count, 0) as current_connection_count
from aggregated
inner join latest_label
    on
        aggregated.company_key = latest_label.company_key
        and latest_label.label_recency = 1
left join current_connections
    on aggregated.company_key = current_connections.company_key
