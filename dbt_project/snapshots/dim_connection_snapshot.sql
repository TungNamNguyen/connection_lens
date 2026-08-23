{% snapshot dim_connection %}

{{
    config(
        target_schema='gold',
        unique_key='connection_id',
        strategy='check',
        check_cols=['company', 'position'],
        hard_deletes='invalidate'
    )
}}

/*
    Gold: SCD Type 2 history of every connection.

    Two non-negotiable configs (§5, §17, §18):

    * `hard_deletes='invalidate'` — a connection missing from the latest
      export gets `dbt_valid_to` set and stops being current. Without it a
      departed connection would stay `is_current` forever. No *reason* for the
      disappearance is inferred or stored: LinkedIn gives no such signal.
    * the input is filtered to the **latest snapshot only** — `strategy='check'`
      diffs its input against current snapshot state, so feeding it the full
      Silver history would break the SCD2 logic.
*/

with latest_snapshot as (

    select max(snapshot_ts) as max_snapshot_ts
    from {{ ref('stg_connections') }}

)

select
    stg.connection_id,
    stg.first_name,
    stg.last_name,
    stg.full_name,
    stg.email_address,
    stg.company,
    stg.company_label,
    stg.company_key,
    stg.position,
    stg.connected_on,
    stg.connected_on_date_key,
    stg.snapshot_ts as source_snapshot_ts
from {{ ref('stg_connections') }} as stg
inner join latest_snapshot on stg.snapshot_ts = latest_snapshot.max_snapshot_ts
where stg.is_identifiable

{% endsnapshot %}
