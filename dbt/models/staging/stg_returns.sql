{#
    Staging: returns. 1:1, typed, deduped on return_id. Links to an order line.
#}

with source as (
    select * from {{ source('raw', 'fact_returns') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by return_id
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

select
    return_id,
    order_line_id,
    returned_qty,
    refund_amount,
    return_ts_utc,
    lower(trim(reason_code)) as reason_code,
    loaded_at_utc
from deduped
where _row_num = 1
