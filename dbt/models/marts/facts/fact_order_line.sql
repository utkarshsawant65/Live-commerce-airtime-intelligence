{{
    config(
        materialized='incremental',
        unique_key='order_line_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}

{#
    fact_order_line — GRAIN: one row per product line per order.
    The only high-volume fact, so the only INCREMENTAL one.

    Incremental logic:
      * On an incremental run we reprocess only orders newer than
        (max event already loaded) - incremental_lookback_minutes.
      * The lookback re-attributes LATE-ARRIVING orders: an order that synced after its
        airtime segment but within the lookback window is picked up and (re)attributed
        on the next run. merge + unique_key means a reprocessed row updates in place,
        never duplicates.
      * Recovery story: `dbt run --full-refresh` rebuilds; Snowflake Time Travel /
        zero-copy clone let us test a fix in isolation before applying.

    Point-in-time product join: we join to the dim_product VERSION whose validity window
    contains event_ts_utc. That is what SCD2 is for — the sale is measured against the
    product's category / price-band AS IT WAS WHEN IT AIRED. Orphans (product/channel not
    yet in the dim) fall back to the unknown member surrogate key (-1).

    Transaction price + monetary measures live HERE, never in a dimension.
#}

with attributed as (
    select * from {{ ref('int_orders_attributed_to_airtime') }}

    {% if is_incremental() %}
        -- Reprocess recent + late-arriving rows only.
        where event_ts_utc >= (
            select dateadd('minute', -{{ var('incremental_lookback_minutes') }}, max(event_ts_utc))
            from {{ this }}
        )
    {% endif %}
),

dim_product_pit as (
    select
        product_id,
        product_key,
        valid_from_ts,
        valid_to_ts
    from {{ ref('dim_product') }}
),

dim_channel as (
    select channel_id, channel_key from {{ ref('dim_channel') }}
),

final as (
    select
        a.order_line_id,
        a.order_id,
        -- Surrogate FKs (point-in-time for product). Unknown member -1 keys for orphans.
        coalesce(p.product_key, dpu.product_key) as product_key,
        coalesce(c.channel_key, dcu.channel_key) as channel_key,
        a.attributed_segment_id,
        a.event_ts_utc,
        cast(a.event_ts_utc as date) as event_date,
        -- Measures.
        a.quantity,
        a.unit_price_paid,
        a.gross_amount,
        a.discount_amount,
        a.net_amount,
        a.is_airtime_attributed
    from attributed as a
    left join dim_product_pit as p
        on a.product_id = p.product_id
        and a.event_ts_utc >= p.valid_from_ts
        and a.event_ts_utc < p.valid_to_ts
    left join dim_channel as c
        on a.channel_id = c.channel_id
    -- Unknown-member fallbacks resolved once, cross-joined as scalars.
    cross join (select product_key from {{ ref('dim_product') }} where product_id = -1) as dpu
    cross join (select channel_key from {{ ref('dim_channel') }} where channel_id = -1) as dcu
)

select * from final
