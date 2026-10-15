{#
    dim_product — conformed product dimension, SCD Type 2.

    Reads from the dbt snapshot (dim_product_snapshot), so each row is a point-in-time
    version with valid_from / valid_to / is_current. The surrogate key is per VERSION
    (product_id + dbt_valid_from), so a fact attributes to the product *as it was* when
    the event happened.

    Includes the UNKNOWN MEMBER (key derived from product_id = -1): the row late /
    orphan facts point at until a real product version exists. Keeping the
    unknown member in the dimension means fact joins never silently drop rows.
#}

with snapshotted as (
    select
        product_id,
        product_name,
        category,
        brand,
        list_price,
        list_price_band,
        dbt_valid_from as valid_from_ts,
        dbt_valid_to as valid_to_ts,
        (dbt_valid_to is null) as is_current
    from {{ ref('dim_product_snapshot') }}
),

ranked as (
    select
        *,
        row_number() over (partition by product_id order by valid_from_ts asc) as version_rank
    from snapshotted
),

with_keys as (
    select
        {{ surrogate_key(['product_id', 'valid_from_ts']) }} as product_key,
        product_id,
        product_name,
        category,
        brand,
        list_price,
        list_price_band,
        -- The EARLIEST version of each product is open-ended on the low end (far-past
        -- valid_from), so a sale that occurred before the snapshot era still resolves to
        -- the correct (first) version rather than dropping to the unknown member. Later
        -- versions keep their real cutover timestamps, preserving SCD2 point-in-time history.
        case when version_rank = 1
            then cast('1900-01-01' as timestamp)
            else valid_from_ts
        end as valid_from_ts,
        -- Open-ended current versions get a far-future valid_to for clean BETWEEN joins.
        coalesce(valid_to_ts, cast('9999-12-31' as timestamp)) as valid_to_ts,
        is_current
    from ranked
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'", "cast('1900-01-01' as timestamp)"]) }} as product_key,
        -1 as product_id,
        'Unknown / Organic' as product_name,
        'Unknown' as category,
        'Unknown' as brand,
        cast(0 as number(12, 2)) as list_price,
        'unknown' as list_price_band,
        cast('1900-01-01' as timestamp) as valid_from_ts,
        cast('9999-12-31' as timestamp) as valid_to_ts,
        true as is_current
)

select * from with_keys
union all
select * from unknown_member
