{# Per order line, total returned quantity never exceeds the quantity sold. #}

with returns_per_line as (
    select
        order_line_id,
        sold_qty,
        sum(returned_qty) as total_returned_qty
    from {{ ref('fact_returns') }}
    group by order_line_id, sold_qty
)

select
    order_line_id,
    sold_qty,
    total_returned_qty,
    total_returned_qty / nullif(sold_qty, 0) as return_rate
from returns_per_line
where total_returned_qty > sold_qty
