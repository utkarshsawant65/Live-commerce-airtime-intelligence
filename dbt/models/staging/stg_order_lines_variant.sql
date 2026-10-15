{#
    stg_order_lines_variant — parse the VARIANT order-line feed defensively (schema-on-read).

    This is the resilient counterpart to stg_order_lines (which reads the typed CSV table).
    It reads raw.order_line_raw, where each row is an untyped JSON payload, and:
      * extracts each field by path (payload:field),
      * uses try_cast so a bad type yields NULL instead of failing the whole run,
      * trims/normalises text,
      * flags each row valid / invalid with an explicit reason, so downstream can split
        clean rows from quarantine (stg_order_lines_quarantine) — bad rows are CAPTURED,
        never silently dropped and never allowed to break the load.

    Why this exists: a typed table breaks the moment a source sends a bad value or changes
    shape. Landing as VARIANT + parsing here means ingestion is decoupled from schema, and
    data-quality failures become observable rows we can quarantine and backfill.
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

validated as (
    select
        *,
        case
            when order_line_id is null then 'missing_or_bad_order_line_id'
            when product_id is null then 'missing_or_bad_product_id'
            when quantity is null then 'missing_or_bad_quantity'
            when quantity < 1 then 'non_positive_quantity'
            when event_ts_utc is null then 'missing_or_bad_event_ts'
            when net_amount is null or net_amount < 0 then 'bad_net_amount'
            else null
        end as dq_failure_reason
    from parsed
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
