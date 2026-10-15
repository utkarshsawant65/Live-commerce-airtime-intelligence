{#
    dim_date — one row per calendar day.

    Built with dbt_utils.date_spine (not a hand-maintained table) so the calendar is
    generated, gap-free, and clipped to the actual data range. The spine is generated
    over a fixed, generous range (literal dates) and then filtered to the data window —
    we do NOT pass a CTE reference into the date_spine macro, because the macro nests its
    own query and an outer CTE is not in scope there on Snowflake.
#}

with bounds as (
    select
        dateadd('day', -2, cast(min(event_ts_utc) as date)) as min_date,
        dateadd('day', 2, cast(max(event_ts_utc) as date)) as max_date
    from {{ ref('stg_order_lines') }}
),

spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2020-01-01' as date)",
        end_date="cast('2030-01-01' as date)"
    ) }}
),

clipped as (
    select cast(spine.date_day as date) as date_day
    from spine
    cross join bounds
    where cast(spine.date_day as date) between bounds.min_date and bounds.max_date
)

select
    date_day,
    {{ surrogate_key(['date_day']) }} as date_key,
    year(date_day) as calendar_year,
    quarter(date_day) as calendar_quarter,
    month(date_day) as calendar_month,
    monthname(date_day) as month_name,
    week(date_day) as calendar_week,
    day(date_day) as day_of_month,
    dayofweek(date_day) as day_of_week,
    dayname(date_day) as day_name,
    case when dayofweek(date_day) in (0, 6) then true else false end as is_weekend
from clipped
