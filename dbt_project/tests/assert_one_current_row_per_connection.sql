{#-
    SCD2 integrity: a connection may have many historical versions but at most
    one open (current) row. Catches a snapshot fed with more than the latest
    Silver snapshot, or a broken unique_key.
-#}
select
    connection_id,
    count(*) as current_row_count
from {{ ref('dim_connection') }}
where dbt_valid_to is null
group by connection_id
having count(*) > 1
