{#
    SCD Type 2 snapshot for products.

    Why a snapshot here and ONLY here: we need point-in-time truth for products —
    when an order aired, what was the product's category and list-price band *at that
    moment*? Category reclassification and price-band moves happen, and a sale must be
    analysed against the product state at time of air. Presenter/show attributes don't
    change meaningfully over the window, so they stay SCD1 (overwrite). Historising
    everything would be over-engineering.

    Strategy: `check` on the attributes that actually slowly change. We deliberately do
    NOT track loaded_at_utc or is_current (audit/derived noise) — only true business
    attributes, so a re-load of unchanged data does not spuriously create a new version.

    Note on the source: the generator already emits versioned rows, but the snapshot is
    what captures change over time as dbt sees it on each run (a new row when a tracked
    column changes). We snapshot the CURRENT row per product from staging; as the
    generator evolves a product's attributes across loads, dbt records the
    dbt_valid_from/to history.
#}

{% snapshot dim_product_snapshot %}

{{
    config(
        target_schema='snapshots',
        unique_key='product_id',
        strategy='check',
        check_cols=['category', 'list_price_band', 'list_price', 'brand', 'product_name'],
        invalidate_hard_deletes=True
    )
}}

-- Snapshot the current version of each product. dbt tracks slowly-changing attributes
-- and stamps dbt_valid_from / dbt_valid_to to build the Type 2 history.
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
