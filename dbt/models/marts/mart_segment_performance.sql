{#
    One row per airtime segment with the orders, units and net revenue attributed to it,
    and revenue per broadcast minute.
#}

with segments as (
    select
        segment_id,
        product_key,
        presenter_key,
        show_key,
        channel_key,
        start_ts_utc,
        broadcast_date,
        is_prime_time,
        daypart,
        segment_duration_minutes
    from {{ ref('fact_airtime_segment') }}
),

attributed_orders as (
    select
        attributed_segment_id,
        count(distinct order_id) as attributed_orders,
        sum(quantity) as attributed_units,
        sum(net_amount) as attributed_revenue
    from {{ ref('fact_order_line') }}
    where is_airtime_attributed = true
    group by attributed_segment_id
)

select
    s.segment_id,
    s.product_key,
    s.presenter_key,
    s.show_key,
    s.channel_key,
    s.start_ts_utc,
    s.broadcast_date,
    s.is_prime_time,
    s.daypart,
    s.segment_duration_minutes,
    coalesce(a.attributed_orders, 0) as attributed_orders,
    coalesce(a.attributed_units, 0) as attributed_units,
    coalesce(a.attributed_revenue, 0) as attributed_revenue,
    -- segment_duration_minutes >= 1 is tested; nullif only guards the division.
    coalesce(a.attributed_revenue, 0) / nullif(s.segment_duration_minutes, 0)
        as revenue_per_broadcast_minute
from segments as s
left join attributed_orders as a on s.segment_id = a.attributed_segment_id
