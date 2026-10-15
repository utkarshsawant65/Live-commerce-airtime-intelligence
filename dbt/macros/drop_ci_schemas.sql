{#
    drop_ci_schemas — tear down the CI-suffixed schemas so an automated run starts from a
    genuinely empty namespace (models AND seeds rebuild cleanly, no "object already
    exists" collisions on --full-refresh).

    SAFETY: this only runs when DBT_SCHEMA_SUFFIX is set to a non-empty value (CI sets it
    to "_CI"). If the suffix is empty — i.e. local dev or any non-CI target — the macro is
    a no-op, so it can NEVER drop the real STAGING / MARTS / SNAPSHOTS schemas.
#}
{% macro drop_ci_schemas() %}
    {%- set suffix = env_var('DBT_SCHEMA_SUFFIX', '') -%}
    {%- if suffix == '' -%}
        {{ log("DBT_SCHEMA_SUFFIX is empty — skipping schema teardown (safety no-op).", info=True) }}
    {%- else -%}
        {%- set schemas = ['staging', 'intermediate', 'marts'] -%}
        {%- for s in schemas -%}
            {%- set fq = target.database ~ '.' ~ s ~ suffix -%}
            {{ log("Dropping schema " ~ fq, info=True) }}
            {% do run_query('drop schema if exists ' ~ fq ~ ' cascade') %}
        {%- endfor -%}
    {%- endif -%}
{% endmacro %}
