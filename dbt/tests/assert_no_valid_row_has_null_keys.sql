{# Rows the VARIANT parser marks valid must have every required field present and in range. #}

select
    order_line_id,
    product_id,
    quantity,
    event_ts_utc
from {{ ref('stg_order_lines_variant') }}
where
    is_valid = true
    and (
        order_line_id is null
        or product_id is null
        or quantity is null
        or quantity < 1
        or event_ts_utc is null
        or net_amount is null
        or net_amount < 0
    )
