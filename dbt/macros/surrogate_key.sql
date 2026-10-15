{#
    A small surrogate-key macro of our own.

    Thin, intentional wrapper over dbt_utils.generate_surrogate_key so that:
      * surrogate-key construction is named and consistent across the project,
      * the hashing/null-handling convention lives in ONE place (if we ever need to
        change it, every key changes together), and
      * model SQL reads as `{{ surrogate_key([...]) }}` — self-documenting intent.

    Convention: null components are coalesced to a literal sentinel before hashing so
    that (a, null) and (a, '') do not collide, matching dbt_utils' own behaviour.
#}
{% macro surrogate_key(field_list) -%}
    {{ dbt_utils.generate_surrogate_key(field_list) }}
{%- endmacro %}
