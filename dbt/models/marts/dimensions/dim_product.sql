{#
    Product dimension, SCD2 on event time: one row per source version, valid over
    [valid_from_ts, valid_to_ts) as dated by the source. Facts join to the version valid
    at their event time. dim_product_snapshot keeps a separate capture-time audit trail.
    Includes the unknown member (product_id = -1) for orphan facts.
#}

with versions as (
    select
        {{ surrogate_key(['product_id', 'product_version']) }} as product_key,
        product_id,
        product_version,
        product_name,
        category,
        brand,
        list_price,
        list_price_band,
        cast(valid_from_date as timestamp_ntz) as valid_from_ts,
        cast(valid_to_date as timestamp_ntz) as valid_to_ts,
        is_current
    from {{ ref('stg_products') }}
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'", "'0'"]) }} as product_key,
        -1 as product_id,
        0 as product_version,
        'Unknown' as product_name,
        'Unknown' as category,
        'Unknown' as brand,
        cast(0 as number(12, 2)) as list_price,
        'unknown' as list_price_band,
        cast('1900-01-01' as timestamp_ntz) as valid_from_ts,
        cast('9999-12-31' as timestamp_ntz) as valid_to_ts,
        true as is_current
)

select * from versions
union all
select * from unknown_member
