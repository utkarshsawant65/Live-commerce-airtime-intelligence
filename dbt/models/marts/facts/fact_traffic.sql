{#
    fact_traffic — GRAIN: one row per channel per minute.
    Measures: concurrent_viewers, web_sessions. The conversion DENOMINATOR:
    without it "conversion lift" has no "conversion of what?" answer.
    Table materialization.

    Joined to dim_channel (surrogate FK) and dim_time_minute (minute-of-day key) so the
    BI model can slice traffic by channel and by daypart / prime-time.
#}

with traffic as (
    select * from {{ ref('stg_traffic') }}
),

dim_channel as (
    select channel_id, channel_key from {{ ref('dim_channel') }}
)

select
    t.channel_id,
    coalesce(c.channel_key, dcu.channel_key) as channel_key,
    t.minute_ts_utc,
    cast(t.minute_ts_utc as date) as traffic_date,
    {{ surrogate_key(['hour(t.minute_ts_utc)', 'minute(t.minute_ts_utc)']) }} as time_minute_key,
    t.concurrent_viewers,
    t.web_sessions
from traffic as t
left join dim_channel as c on t.channel_id = c.channel_id
cross join (select channel_key from {{ ref('dim_channel') }} where channel_id = -1) as dcu
