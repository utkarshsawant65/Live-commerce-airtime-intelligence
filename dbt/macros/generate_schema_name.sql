{#
    Override dbt's default schema naming.

    Default dbt behaviour prefixes the target schema, producing names like
    `dbt_staging` or `analytics_marts`. We want clean, environment-agnostic schema
    names that match the Snowflake setup (RAW / STAGING / INTERMEDIATE / MARTS /
    SNAPSHOTS), because BI users, RBAC grants, and the "BI reads from MARTS only"
    rule all reference those literal names.

    Behaviour: if a model/seed/snapshot sets a custom schema (+schema: marts), use
    that name verbatim. Otherwise fall back to the profile's target schema.

    This keeps the warehouse namespace identical across dev and prod targets, so a
    grant on MARTS or a Power BI connection string never has to change per environment.

    CI isolation: the env var DBT_SCHEMA_SUFFIX (CI sets it to "_CI") suffixes every
    schema, so an automated CI run builds into STAGING_CI / MARTS_CI / ... — its own
    namespace, never racing with or clobbering interactive local dev in the shared
    database. Local dev leaves the var unset and uses the clean names.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set suffix = env_var('DBT_SCHEMA_SUFFIX', '') -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}{{ suffix }}
    {%- else -%}
        {{ custom_schema_name | trim }}{{ suffix }}
    {%- endif -%}
{%- endmacro %}
