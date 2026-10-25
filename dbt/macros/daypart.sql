{# Time-of-day bands, defined once. Prime time 18:00-22:59 matches the generator. #}

{% macro daypart(hour_expr) -%}
    case
        when {{ hour_expr }} between 5 and 11 then 'morning'
        when {{ hour_expr }} between 12 and 17 then 'afternoon'
        when {{ hour_expr }} between 18 and 22 then 'prime_time'
        else 'late_night'
    end
{%- endmacro %}

{% macro is_prime_time(hour_expr) -%}
    ({{ hour_expr }} between 18 and 22)
{%- endmacro %}
