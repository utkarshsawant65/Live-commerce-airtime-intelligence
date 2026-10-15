{{
    config(
        materialized='incremental',
        unique_key='order_line_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}

{#
    One row per product line per order. Incremental merge on order_line_id.
    Each run re-processes rows within incremental_lookback_minutes of the latest event
    already loaded (to re-attribute recent orders) plus any row loaded since the last run,
    whatever its event time (late-arriving data).
    product_key is the dim_product version valid at event_ts_utc.
#}

with attributed as (
    select * from {{ ref('int_orders_attributed_to_airtime') }}

    {% if is_incremental() %}
        where event_ts_utc >= (
            select dateadd('minute', -{{ var('incremental_lookback_minutes') }}, max(t.event_ts_utc))
            from {{ this }} as t
        )
        or loaded_at_utc > (select max(t.loaded_at_utc) from {{ this }} as t)
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
    select
        channel_id,
        channel_key
    from {{ ref('dim_channel') }}
),

final as (
    select
        a.order_line_id,
        a.order_id,
        coalesce(p.product_key, dpu.product_key) as product_key,
        coalesce(c.channel_key, dcu.channel_key) as channel_key,
        a.attributed_segment_id,
        a.event_ts_utc,
        cast(a.event_ts_utc as date) as event_date,
        a.quantity,
        a.unit_price_paid,
        a.gross_amount,
        a.discount_amount,
        a.net_amount,
        a.is_airtime_attributed,
        a.loaded_at_utc
    from attributed as a
    left join dim_product_pit as p
        on
            a.product_id = p.product_id
            and a.event_ts_utc >= p.valid_from_ts
            and a.event_ts_utc < p.valid_to_ts
    left join dim_channel as c
        on a.channel_id = c.channel_id
    -- Unknown-member (-1) keys for orphans.
    cross join (
        select product_key from {{ ref('dim_product') }}
        where product_id = -1
    ) as dpu
    cross join (
        select channel_key from {{ ref('dim_channel') }}
        where channel_id = -1
    ) as dcu
)

select * from final
