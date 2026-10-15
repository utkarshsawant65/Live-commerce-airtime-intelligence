{#
    Rows from the VARIANT order-line feed that failed validation, with the reason.
    Not deduplicated: every failing payload is kept for inspection.
#}

select
    order_line_id,
    order_id,
    product_id,
    channel_id,
    event_ts_utc,
    quantity,
    source_file,
    loaded_at_utc,
    dq_failure_reason
from {{ ref('stg_order_lines_variant') }}
where is_valid = false
