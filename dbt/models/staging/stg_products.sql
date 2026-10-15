{# Product versions as emitted by the source, deduped on (product_id, product_version). #}

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
