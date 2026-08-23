{#-
    Use the configured schema name verbatim (silver / gold / mart) instead of
    dbt's default `<target_schema>_<custom_schema>` concatenation, so the
    medallion layers read cleanly in DuckDB.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
