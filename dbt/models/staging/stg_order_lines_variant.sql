{#
    Order lines from the JSON feed (raw.order_line_raw, VARIANT). Fields are extracted
    with try_cast so a bad value becomes null instead of failing the run; each row is
    flagged is_valid with a dq_failure_reason. Invalid rows go to stg_order_lines_quarantine.
#}

with raw_payloads as (
    select
        payload,
        source_file,
        loaded_at_utc
    from {{ source('raw', 'order_line_raw') }}
),

parsed as (
    select
        try_cast(payload:order_line_id::string as integer) as order_line_id,
        try_cast(payload:order_id::string as integer) as order_id,
        try_cast(payload:product_id::string as integer) as product_id,
        try_cast(payload:channel_id::string as integer) as channel_id,
        try_cast(payload:event_ts_utc::string as timestamp_ntz) as event_ts_utc,
        try_cast(payload:quantity::string as integer) as quantity,
        try_cast(payload:unit_price_paid::string as number(12, 2)) as unit_price_paid,
        try_cast(payload:gross_amount::string as number(12, 2)) as gross_amount,
        try_cast(payload:discount_amount::string as number(12, 2)) as discount_amount,
        try_cast(payload:net_amount::string as number(12, 2)) as net_amount,
        source_file,
        loaded_at_utc
    from raw_payloads
),

known_products as (
    select distinct product_id
    from {{ ref('stg_products') }}
),

validated as (
    select
        p.*,
        case
            when p.order_line_id is null then 'missing_or_bad_order_line_id'
            when p.product_id is null then 'missing_or_bad_product_id'
            when p.quantity is null then 'missing_or_bad_quantity'
            when p.quantity < 1 then 'non_positive_quantity'
            when p.event_ts_utc is null then 'missing_or_bad_event_ts'
            when p.net_amount is null or p.net_amount < 0 then 'bad_net_amount'
            when k.product_id is null then 'unknown_product_id'
            when row_number() over (
                partition by p.order_line_id
                order by p.loaded_at_utc, p.source_file
            ) > 1 then 'duplicate_order_line'
        end as dq_failure_reason
    from parsed as p
    left join known_products as k on p.product_id = k.product_id
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
    source_file,
    loaded_at_utc,
    (dq_failure_reason is null) as is_valid,
    dq_failure_reason
from validated
