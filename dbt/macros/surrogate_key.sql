{# Project-wide surrogate key; wraps dbt_utils.generate_surrogate_key so the convention lives in one place. #}
{% macro surrogate_key(field_list) -%}
    {{ dbt_utils.generate_surrogate_key(field_list) }}
{%- endmacro %}
