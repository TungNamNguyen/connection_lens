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
        company_key,
        company_label,
        position,
        connected_on
    from {{ ref('fct_connection_snapshot') }}

),

company_counts as (

    /* Grouped by `company_key`, not by the raw label: `company_key` is the
       conformed employer identity every other model already joins on, and it
       folds case/whitespace variants together. Grouping by the raw text here
       instead would split one employer across several bars and rank it below
       its true size. */
    select
        snapshot_ts,
        company_key,
        count(*) as connection_count
    from fct
    group by snapshot_ts, company_key

),

company_display_labels as (

    /* Which spelling to show for a key that has several. The variant the most
       connections carry wins, alphabetical order breaking ties so the result
       is deterministic. `max(company_label)` would sort alphabetically and so
       could pick a lower-cased typo over the properly written name. */
    select
        snapshot_ts,
        company_key,
        company_label
    from (
        select
            snapshot_ts,
            company_key,
            company_label,
            row_number() over (
                partition by snapshot_ts, company_key
                order by count(*) desc, company_label asc
            ) as label_rank
        from fct
        group by snapshot_ts, company_key, company_label
    ) as ranked_labels
    where label_rank = 1

),

by_company as (

    select
        company_counts.snapshot_ts,
        'company' as dimension_type,
        display_labels.company_label as dimension_value,
        company_counts.connection_count
    from company_counts
    inner join company_display_labels as display_labels
        on
            company_counts.snapshot_ts = display_labels.snapshot_ts
            and company_counts.company_key = display_labels.company_key

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
