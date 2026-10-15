{#
    fact_returns — GRAIN: one row per returned order line.
    Measures: returned_qty, refund_amount. Links to fact_order_line via order_line_id.
    Table materialization (low volume).

    We carry the order line's product_key/channel_key onto the return so returns can be
    sliced by the same conformed dims without re-joining order lines downstream (the
    return is attributed to the SAME product version / channel as the original sale).
#}

with returns as (
    select * from {{ ref('stg_returns') }}
),

order_lines as (
    select
        order_line_id,
        product_key,
        channel_key,
        attributed_segment_id,
        quantity as sold_qty
    from {{ ref('fact_order_line') }}
)

select
    r.return_id,
    r.order_line_id,
    o.product_key,
    o.channel_key,
    o.attributed_segment_id,
    r.return_ts_utc,
    cast(r.return_ts_utc as date) as return_date,
    r.reason_code,
    r.returned_qty,
    o.sold_qty,
    r.refund_amount
from returns as r
inner join order_lines as o on r.order_line_id = o.order_line_id
