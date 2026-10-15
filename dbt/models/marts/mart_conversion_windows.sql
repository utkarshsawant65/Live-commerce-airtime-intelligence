{#
    mart_conversion_windows — conversion rate + lift, on-air vs off-air.

    GRAIN: one row per (airtime_state, event_date, channel_key, daypart).

    Both numerator and denominator are valid at this grain:
      * orders     — attributable to date, channel, and daypart (from fact_order_line)
      * web_sessions — attributable to date, channel, and daypart (from fact_traffic)
    so conversion_rate = orders ÷ web_sessions is a real rate that rolls up correctly to
    any of those dimensions by re-aggregating (SUM orders ÷ SUM sessions) — which is why
    the rate is NOT pre-divided per row for roll-ups; we expose the additive counts and
    let BI/DAX compute the ratio in filter context (DIVIDE(SUM(orders), SUM(sessions))).

    NOTE on grain choice: we deliberately do NOT split by product/category, because a web
    session is not tied to a product — there is no honest per-product denominator. The
    population grain (state × date × channel × daypart) is the defensible one; per-category
    conversion is left to a clearly-labelled DAX index in the report, not faked here.

    on_air = an order attributed to a segment; a traffic minute covered by any segment's
    window [start, end + lag]. Scope = online channels with web_sessions > 0 (broadcast
    has viewers, not sessions), so the orders÷sessions rate is like-for-like.
#}

with order_lines as (
    select
        order_id,
        channel_key,
        event_ts_utc,
        cast(event_ts_utc as date) as event_date,
        is_airtime_attributed
    from {{ ref('fact_order_line') }}
),

orders_by_grain as (
    select
        case when is_airtime_attributed then 'on_air' else 'off_air' end as airtime_state,
        event_date,
        channel_key,
        case
            when hour(event_ts_utc) between 5 and 11 then 'morning'
            when hour(event_ts_utc) between 12 and 17 then 'afternoon'
            when hour(event_ts_utc) between 18 and 22 then 'prime_time'
            else 'late_night'
        end as daypart,
        count(distinct order_id) as orders
    from order_lines
    group by 1, 2, 3, 4
),

-- Each online traffic minute: on-air if any segment's window+lag covers it, plus its
-- date / channel / daypart so sessions aggregate to the same grain as orders.
traffic_classified as (
    select
        t.channel_key,
        t.traffic_date as event_date,
        t.web_sessions,
        case
            when hour(t.minute_ts_utc) between 5 and 11 then 'morning'
            when hour(t.minute_ts_utc) between 12 and 17 then 'afternoon'
            when hour(t.minute_ts_utc) between 18 and 22 then 'prime_time'
            else 'late_night'
        end as daypart,
        case when exists (
            select 1
            from {{ ref('fact_airtime_segment') }} as s
            where t.minute_ts_utc >= s.start_ts_utc
                and t.minute_ts_utc < dateadd('minute', {{ var('attribution_lag_minutes') }}, s.end_ts_utc)
        ) then 'on_air' else 'off_air' end as airtime_state
    from {{ ref('fact_traffic') }} as t
    where t.web_sessions > 0
),

sessions_by_grain as (
    select
        airtime_state,
        event_date,
        channel_key,
        daypart,
        sum(web_sessions) as web_sessions
    from traffic_classified
    group by 1, 2, 3, 4
)

-- Full outer join: keep grains that have orders but no sessions (and vice versa), so the
-- mart never silently drops a slice. Missing counterpart -> 0.
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
    on o.airtime_state = s.airtime_state
    and o.event_date = s.event_date
    and o.channel_key = s.channel_key
    and o.daypart = s.daypart
