{{ config(materialized='table', tags=['gold']) }}

/*
    Gold conformed date dimension, spanning the oldest connection date through
    the most recent export snapshot. Generated from the data itself so the
    spine never needs manual extension.
*/

with bounds as (

    select
        coalesce(min(connected_on), current_date) as start_date,
        greatest(
            coalesce(max(connected_on), current_date),
            coalesce(max(snapshot_date), current_date)
        ) as end_date
    from {{ ref('stg_connections') }}

),

spine as (

    {{ date_spine(
        '(select start_date from bounds)',
        '(select end_date from bounds)'
    ) }}

)

select
    {{ date_key('date_day') }} as date_key,
    date_day,
    cast(extract(year from date_day) as integer) as calendar_year,
    cast(extract(quarter from date_day) as integer) as calendar_quarter,
    cast(extract(month from date_day) as integer) as calendar_month,
    strftime(date_day, '%B') as month_name,
    strftime(date_day, '%Y-%m') as year_month,
    cast(extract(day from date_day) as integer) as day_of_month,
    cast(extract(isodow from date_day) as integer) as iso_day_of_week,
    strftime(date_day, '%A') as day_name,
    cast(extract(week from date_day) as integer) as iso_week,
    cast(date_trunc('month', date_day) as date) as month_start_date,
    cast(last_day(date_day) as date) as month_end_date,
    extract(isodow from date_day) >= 6 as is_weekend
from spine
