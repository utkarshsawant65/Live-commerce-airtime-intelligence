{#
    One row per calendar day, covering every date that appears in orders, returns,
    segments or traffic. The spine uses literal bounds and is then clipped, because
    dbt_utils.date_spine cannot take a CTE reference as its start/end.
#}

with source_ranges as (
    select
        min(event_ts_utc) as min_ts,
        max(event_ts_utc) as max_ts
    from {{ ref('stg_order_lines') }}
    union all
    select
        min(return_ts_utc) as min_ts,
        max(return_ts_utc) as max_ts
    from {{ ref('stg_returns') }}
    union all
    select
        min(start_ts_utc) as min_ts,
        max(end_ts_utc) as max_ts
    from {{ ref('stg_airtime_segments') }}
    union all
    select
        min(minute_ts_utc) as min_ts,
        max(minute_ts_utc) as max_ts
    from {{ ref('stg_traffic') }}
),

bounds as (
    select
        cast(min(min_ts) as date) as min_date,
        cast(max(max_ts) as date) as max_date
    from source_ranges
),

spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2000-01-01' as date)",
        end_date="cast('2050-01-01' as date)"
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
    dayofweek(date_day) in (0, 6) as is_weekend
from clipped
