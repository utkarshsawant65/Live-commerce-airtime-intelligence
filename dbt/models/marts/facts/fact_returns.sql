{#
    One row per returned order line. Carries the sale's product_key, channel_key and
    attributed segment so returns slice by the same dimensions as the sale.
    Inner join: a return whose order line is not in fact_order_line is excluded
    (stg_returns has a relationships test against stg_order_lines).
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
