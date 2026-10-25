{#
    One row per minute of the day (1440 rows), for slicing by time of day.
    Daypart and prime time come from macros/daypart.sql.
#}

with minutes as (
    {{ dbt_utils.date_spine(
        datepart="minute",
        start_date="cast('2000-01-01' as timestamp)",
        end_date="cast('2000-01-02' as timestamp)"
    ) }}
)

select
    {{ surrogate_key(['hour(date_minute)', 'minute(date_minute)']) }} as time_minute_key,
    hour(date_minute) as hour_of_day,
    minute(date_minute) as minute_of_hour,
    (hour(date_minute) * 60 + minute(date_minute)) as minute_of_day,
    {{ is_prime_time('hour(date_minute)') }} as is_prime_time,
    {{ daypart('hour(date_minute)') }} as daypart
from minutes
