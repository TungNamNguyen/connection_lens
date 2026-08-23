{{ config(materialized='table', tags=['mart']) }}

/*
    Data mart powering the Network Stats tab's distribution charts: a tidy
    (snapshot_ts, dimension_type, dimension_value, connection_count) table so
    a new breakdown is one more UNION branch, not a new model.

    Role tagging is deliberately absent here: the taxonomy lives in
    `streamlit_app/tagging.py` as one testable function (§9), and duplicating
    its keyword lists in SQL would guarantee drift.
*/

with fct as (

    select
        connection_id,
        snapshot_ts,
        company_label,
        position,
        connected_on
    from {{ ref('fct_connection_snapshot') }}

),

by_company as (

    select
        snapshot_ts,
        'company' as dimension_type,
        company_label as dimension_value,
        count(*) as connection_count
    from fct
    group by snapshot_ts, company_label

),

by_position as (

    select
        snapshot_ts,
        'position' as dimension_type,
        coalesce(position, '(unknown)') as dimension_value,
        count(*) as connection_count
    from fct
    group by snapshot_ts, coalesce(position, '(unknown)')

),

by_connected_month as (

    select
        snapshot_ts,
        'connected_year_month' as dimension_type,
        strftime(connected_on, '%Y-%m') as dimension_value,
        count(*) as connection_count
    from fct
    group by snapshot_ts, strftime(connected_on, '%Y-%m')

),

combined as (

    select
        snapshot_ts,
        dimension_type,
        dimension_value,
        connection_count
    from by_company
    union all
    select
        snapshot_ts,
        dimension_type,
        dimension_value,
        connection_count
    from by_position
    union all
    select
        snapshot_ts,
        dimension_type,
        dimension_value,
        connection_count
    from by_connected_month

)

select
    snapshot_ts,
    dimension_type,
    dimension_value,
    connection_count,
    row_number() over (
        partition by snapshot_ts, dimension_type
        order by connection_count desc, dimension_value asc
    ) as rank_within_dimension,
    round(
        100.0 * connection_count
        / sum(connection_count) over (partition by snapshot_ts, dimension_type),
        2
    ) as share_pct
from combined
