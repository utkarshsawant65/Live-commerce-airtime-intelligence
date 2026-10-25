{# Every order line joins to the product version whose validity covers its event time. #}

select
    f.order_line_id,
    f.event_ts_utc,
    p.product_id,
    p.product_version,
    p.valid_from_ts,
    p.valid_to_ts
from {{ ref('fact_order_line') }} as f
inner join {{ ref('dim_product') }} as p on f.product_key = p.product_key
where
    p.product_id != -1
    and not (f.event_ts_utc >= p.valid_from_ts and f.event_ts_utc < p.valid_to_ts)
