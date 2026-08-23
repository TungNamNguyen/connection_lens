{{
    config(
        materialized='table',
        tags=['silver']
    )
}}

/*
    Silver: clean and type the raw export.

    Grain: one row per connection per snapshot_ts — no cross-snapshot
    comparison happens here, that is Gold's job (§7, §17).

    Rows whose LinkedIn URL is blank (LinkedIn exports a date-only row for
    restricted/deactivated profiles) cannot be given a stable identity. They
    are kept here and flagged via `is_identifiable` so they stay countable,
    and are excluded from Gold rather than dropped silently.
*/

with bronze as (

    select
        first_name,
        last_name,
        url,
        email_address,
        company,
        position,
        connected_on,
        snapshot_ts,
        file_hash,
        source_object,
        source_row_number,
        ingested_at
    from {{ source('bronze', 'raw_connections') }}

),

cleaned as (

    select
        nullif(trim(url), '') as connection_id,
        nullif(trim(first_name), '') as first_name,
        nullif(trim(last_name), '') as last_name,
        nullif(trim(email_address), '') as email_address,
        nullif(trim(company), '') as company,
        nullif(trim(position), '') as position,
        {{ parse_linkedin_date('connected_on') }} as connected_on,
        nullif(trim(connected_on), '') as connected_on_raw,
        cast(snapshot_ts as timestamp) as snapshot_ts,
        file_hash,
        source_object,
        source_row_number,
        cast(ingested_at as timestamp) as ingested_at
    from bronze

),

deduplicated as (

    select
        connection_id,
        first_name,
        last_name,
        email_address,
        company,
        position,
        connected_on,
        connected_on_raw,
        snapshot_ts,
        file_hash,
        source_object,
        source_row_number,
        ingested_at,
        row_number() over (
            partition by snapshot_ts, connection_id
            order by source_row_number
        ) as row_number_within_snapshot
    from cleaned

),

final as (

    select
        connection_id,
        first_name,
        last_name,
        nullif(trim(concat_ws(' ', first_name, last_name)), '') as full_name,
        email_address,
        company,
        coalesce(company, '(unknown)') as company_label,
        {{ normalise_company("coalesce(company, '(unknown)')") }} as company_normalised,
        {{ dbt_utils.generate_surrogate_key([
            normalise_company("coalesce(company, '(unknown)')")
        ]) }} as company_key,
        position,
        lower(position) as position_normalised,
        connected_on,
        connected_on_raw,
        {{ date_key('connected_on') }} as connected_on_date_key,
        snapshot_ts,
        cast(snapshot_ts as date) as snapshot_date,
        {{ date_key('snapshot_ts') }} as snapshot_date_key,
        file_hash,
        source_object,
        source_row_number,
        ingested_at,
        connection_id is not null as is_identifiable,
        email_address is not null as has_email
    from deduplicated
    where
        connection_id is null
        or row_number_within_snapshot = 1

)

select
    connection_id,
    first_name,
    last_name,
    full_name,
    email_address,
    company,
    company_label,
    company_normalised,
    company_key,
    position,
    position_normalised,
    connected_on,
    connected_on_raw,
    connected_on_date_key,
    snapshot_ts,
    snapshot_date,
    snapshot_date_key,
    file_hash,
    source_object,
    source_row_number,
    ingested_at,
    is_identifiable,
    has_email
from final
