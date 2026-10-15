{#
    Tags each order line with the airtime segment it is attributed to.
    A segment qualifies if it features the same product and the order falls in
    [start_ts_utc, end_ts_utc + attribution_lag_minutes). If several qualify, the most
    recent start wins (last touch). No match -> segment -1 (organic).
#}

with orders as (
    select
        order_line_id,
        order_id,
        product_id,
        channel_id,
        event_ts_utc,
        quantity,
        unit_price_paid,
        gross_amount,
        discount_amount,
        net_amount,
        loaded_at_utc
    from {{ ref('stg_order_lines') }}
),

segments as (
    select
        segment_id,
        product_id,
        start_ts_utc,
        dateadd('minute', {{ var('attribution_lag_minutes') }}, end_ts_utc) as attributable_until_ts
    from {{ ref('stg_airtime_segments') }}
),

candidates as (
    select
        o.order_line_id,
        s.segment_id,
        row_number() over (
            partition by o.order_line_id
            order by s.start_ts_utc desc, s.segment_id desc
        ) as candidate_rank
    from orders as o
    inner join segments as s
        on
            o.product_id = s.product_id
            and o.event_ts_utc >= s.start_ts_utc
            and o.event_ts_utc < s.attributable_until_ts
),

best_match as (
    select
        order_line_id,
        segment_id
    from candidates
    where candidate_rank = 1
)

select
    o.order_line_id,
    o.order_id,
    o.product_id,
    o.channel_id,
    o.event_ts_utc,
    o.quantity,
    o.unit_price_paid,
    o.gross_amount,
    o.discount_amount,
    o.net_amount,
    o.loaded_at_utc,
    coalesce(b.segment_id, -1) as attributed_segment_id,
    (b.segment_id is not null) as is_airtime_attributed
from orders as o
left join best_match as b
    on o.order_line_id = b.order_line_id
