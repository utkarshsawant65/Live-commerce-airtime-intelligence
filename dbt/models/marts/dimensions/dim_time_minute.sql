{#
    One row per minute of the day (1440 rows), for slicing by time of day.
    Prime time is 18:00-22:59, matching PRIME_TIME_HOURS in the generator.
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
    hour(date_minute) between 18 and 22 as is_prime_time,
    case
        when hour(date_minute) between 5 and 11 then 'morning'
        when hour(date_minute) between 12 and 17 then 'afternoon'
        when hour(date_minute) between 18 and 22 then 'prime_time'
        else 'late_night'
    end as daypart
from minutes
