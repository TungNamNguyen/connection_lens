{#-
    The "Companies" metric and the Top-companies chart must count the same
    thing. They are computed by different models — `mart_network_stats` counts
    distinct `company_key`, `mart_network_breakdown` emits one row per employer
    — so nothing but this test stops them drifting apart again.

    They did drift once: the breakdown grouped by the raw `company_label`, so a
    single employer written two ways became two bars and two "distinct values",
    while the metric folded them into one key. The two numbers disagreed on
    screen with nothing failing.

    `(unknown)` is excluded on both sides: it is "no employer disclosed", not an
    employer, and is reported as `connections_without_company` instead.
-#}
with breakdown_companies as (

    select
        snapshot_ts,
        count(*) as breakdown_company_count
    from {{ ref('mart_network_breakdown') }}
    where
        dimension_type = 'company'
        and dimension_value <> '(unknown)'
    group by snapshot_ts

),

stats_companies as (

    select
        snapshot_ts,
        distinct_companies
    from {{ ref('mart_network_stats') }}

)

select
    stats.snapshot_ts,
    stats.distinct_companies,
    coalesce(breakdown.breakdown_company_count, 0) as breakdown_company_count
from stats_companies as stats
left join breakdown_companies as breakdown
    on stats.snapshot_ts = breakdown.snapshot_ts
where stats.distinct_companies <> coalesce(breakdown.breakdown_company_count, 0)
