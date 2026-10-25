{#
    Orders and web sessions on online channels, split on-air vs off-air.
    Grain: airtime_state x event_date x channel_key x daypart.
    A minute is on_air if it falls in any segment's window
    [start_ts_utc, end_ts_utc + attribution_lag_minutes); orders are classified by the
    minute of event_ts_utc. Sum orders and web_sessions before dividing when rolling up.
#}

with online_channels as (
    select channel_key
    from {{ ref('dim_channel') }}
    where channel_type = 'online'
),

segment_windows as (
    select
        start_ts_utc,
        dateadd('minute', {{ var('attribution_lag_minutes') }}, end_ts_utc) as window_end_ts
    from {{ ref('fact_airtime_segment') }}
),

order_minutes as (
    select distinct
        f.order_id,
        f.channel_key,
        date_trunc('minute', f.event_ts_utc) as minute_ts_utc
    from {{ ref('fact_order_line') }} as f
    inner join online_channels as c on f.channel_key = c.channel_key
),

traffic_minutes as (
    select
        t.channel_key,
        t.minute_ts_utc,
        t.web_sessions
    from {{ ref('fact_traffic') }} as t
    inner join online_channels as c on t.channel_key = c.channel_key
),

-- Classify each distinct minute once so orders and traffic share the same definition.
minute_state as (
    select
        m.minute_ts_utc,
        iff(count(w.start_ts_utc) > 0, 'on_air', 'off_air') as airtime_state
    from (
        select minute_ts_utc from order_minutes
        union distinct
        select minute_ts_utc from traffic_minutes
    ) as m
    left join segment_windows as w
        on
            m.minute_ts_utc >= w.start_ts_utc
            and m.minute_ts_utc < w.window_end_ts
    group by 1
),

orders_by_grain as (
    select
        ms.airtime_state,
        cast(o.minute_ts_utc as date) as event_date,
        o.channel_key,
        {{ daypart('hour(o.minute_ts_utc)') }} as daypart,
        count(distinct o.order_id) as orders
    from order_minutes as o
    inner join minute_state as ms on o.minute_ts_utc = ms.minute_ts_utc
    group by 1, 2, 3, 4
),

sessions_by_grain as (
    select
        ms.airtime_state,
        cast(t.minute_ts_utc as date) as event_date,
        t.channel_key,
        {{ daypart('hour(t.minute_ts_utc)') }} as daypart,
        sum(t.web_sessions) as web_sessions
    from traffic_minutes as t
    inner join minute_state as ms on t.minute_ts_utc = ms.minute_ts_utc
    group by 1, 2, 3, 4
)

-- Full outer join so a slice with orders but no sessions (or the reverse) is kept.
select
    coalesce(o.airtime_state, s.airtime_state) as airtime_state,
    coalesce(o.event_date, s.event_date) as event_date,
    coalesce(o.channel_key, s.channel_key) as channel_key,
    coalesce(o.daypart, s.daypart) as daypart,
    coalesce(o.orders, 0) as orders,
    coalesce(s.web_sessions, 0) as web_sessions,
    coalesce(o.orders, 0) / nullif(s.web_sessions, 0) as conversion_rate
from orders_by_grain as o
full outer join sessions_by_grain as s
    on
        o.airtime_state = s.airtime_state
        and o.event_date = s.event_date
        and o.channel_key = s.channel_key
        and o.daypart = s.daypart
