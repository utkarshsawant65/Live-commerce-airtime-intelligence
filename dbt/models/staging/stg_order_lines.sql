{# Order lines from the typed CSV feed, deduped on order_line_id (latest load wins). #}

with source as (
    select * from {{ source('raw', 'fact_order_line') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by order_line_id
            order by loaded_at_utc desc, event_ts_utc desc
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
