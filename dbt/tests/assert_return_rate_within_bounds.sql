{#
    Singular test: a product line cannot be returned more than it
    was sold. Per order line, sum(returned_qty) must never exceed sold_qty, so the
    derived return rate is always within [0, 1]. A value > 1 means a data or join error
    (e.g. duplicated returns, wrong grain).

    Returns offending rows; passes when zero rows.
#}

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
