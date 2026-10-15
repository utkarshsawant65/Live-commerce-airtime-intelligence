{#
    Staging: products. 1:1 with raw, cleaned/typed/deduped.

    Raw carries one row per product per version. We keep ALL versions here (the SCD2
    snapshot in Phase 3 needs the history); we only dedupe exact duplicate rows that a
    re-load could introduce, keyed on the natural (product_id, product_version) grain.
#}

with source as (
    select * from {{ source('raw', 'dim_product') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by product_id, product_version
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

select
    product_id,
    product_version,
    trim(product_name) as product_name,
    trim(category) as category,
    trim(brand) as brand,
    list_price,
    lower(trim(list_price_band)) as list_price_band,
    valid_from as valid_from_date,
    valid_to as valid_to_date,
    is_current,
    loaded_at_utc
from deduped
where _row_num = 1
