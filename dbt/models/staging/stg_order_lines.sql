{#
    Staging: order lines. 1:1, typed, deduped on order_line_id.

    The transaction price (unit_price_paid) and monetary measures live HERE in the
    fact lineage, never in a dimension. We surface them
    cleanly and let the marts fact own the grain.
#}

with source as (
    select * from {{ source('raw', 'fact_order_line') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by order_line_id
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

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
from deduped
where _row_num = 1
