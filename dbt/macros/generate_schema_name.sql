{#
    Use the custom schema name verbatim (STAGING, MARTS, ...) instead of dbt's default
    <target_schema>_<custom> prefixing, so grants and BI connections are the same in
    every environment. DBT_SCHEMA_SUFFIX (CI sets '_CI') is appended to isolate CI runs.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set suffix = env_var('DBT_SCHEMA_SUFFIX', '') | trim -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}{{ suffix }}
    {%- else -%}
        {{ custom_schema_name | trim }}{{ suffix }}
    {%- endif -%}
{%- endmacro %}
