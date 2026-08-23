{#-
    DuckDB-specific SQL lives here and nowhere else, so switching adapter is a
    macro rewrite rather than a model rewrite (§11).
-#}

{% macro parse_linkedin_date(column) -%}
    {#- LinkedIn exports "17 Aug 2026"; try_* returns NULL instead of erroring. -#}
    cast(try_strptime(trim({{ column }}), '%d %b %Y') as date)
{%- endmacro %}


{% macro date_key(column) -%}
    {#- Kimball integer date key, e.g. 2026-08-17 -> 20260817. -#}
    cast(strftime(cast({{ column }} as date), '%Y%m%d') as integer)
{%- endmacro %}


{% macro date_spine(start_date_expr, end_date_expr) -%}
    {#- One row per calendar day between two date expressions, inclusive. -#}
    select cast(unnest(generate_series(
        cast(({{ start_date_expr }}) as date),
        cast(({{ end_date_expr }}) as date),
        interval 1 day
    )) as date) as date_day
{%- endmacro %}


{% macro normalise_company(column) -%}
    {#- Squeeze whitespace + lower-case so "Techcombank  (TCB)" and
        "techcombank (TCB)" collapse to one dimension member. -#}
    regexp_replace(lower(trim({{ column }})), '\s+', ' ', 'g')
{%- endmacro %}
