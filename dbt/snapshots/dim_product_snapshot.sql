{#
    SCD2 history for products (check strategy on the current row from staging).
    A new version is opened on the snapshot run that first sees a changed attribute,
    so dbt_valid_from is capture time, not the source's own effective date.
#}

{% snapshot dim_product_snapshot %}

{{
    config(
        unique_key='product_id',
        strategy='check',
        check_cols=['category', 'list_price_band', 'list_price', 'brand', 'product_name'],
        hard_deletes='invalidate'
    )
}}

select
    product_id,
    product_name,
    category,
    brand,
    list_price,
    list_price_band
from {{ ref('stg_products') }}
where is_current = true

{% endsnapshot %}
