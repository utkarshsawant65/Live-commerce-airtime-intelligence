{#
    Drops the CI-suffixed schemas so a CI build starts from an empty namespace.
    No-op unless target.name is 'ci' and DBT_SCHEMA_SUFFIX (trimmed) looks like '_XYZ',
    so it cannot touch the unsuffixed STAGING / MARTS / SNAPSHOTS schemas.
#}
{% macro drop_ci_schemas() %}
    {%- set suffix = env_var('DBT_SCHEMA_SUFFIX', '') | trim -%}
    {%- if target.name != 'ci' or suffix | length < 2 or suffix[:1] != '_' -%}
        {{ log("drop_ci_schemas: skipped (target=" ~ target.name ~ ", suffix='" ~ suffix ~ "'). Requires target 'ci' and a suffix like '_CI'.", info=True) }}
    {%- else -%}
        {%- for s in ['staging', 'intermediate', 'marts', 'snapshots'] -%}
            {%- set fq = target.database ~ '.' ~ s ~ suffix -%}
            {{ log("Dropping schema " ~ fq, info=True) }}
            {% do run_query('drop schema if exists ' ~ fq ~ ' cascade') %}
        {%- endfor -%}
    {%- endif -%}
{% endmacro %}
