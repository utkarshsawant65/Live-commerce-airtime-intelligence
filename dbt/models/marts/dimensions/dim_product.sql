{#
    Product dimension, SCD2, one row per snapshot version. Versions are effective from
    the snapshot run that captured the change. The first version of each product is
    widened back to 1900-01-01 so events before the first snapshot still resolve.
    Includes the unknown member (product_id = -1) for orphan facts.
#}

with snapshotted as (
    select
        product_id,
        product_name,
        category,
        brand,
        list_price,
        list_price_band,
        dbt_valid_from,
        dbt_valid_to,
        row_number() over (partition by product_id order by dbt_valid_from) as version_number
    from {{ ref('dim_product_snapshot') }}
),

versions as (
    select
        {{ surrogate_key(['product_id', 'dbt_valid_from']) }} as product_key,
        product_id,
        product_name,
        category,
        brand,
        list_price,
        list_price_band,
        case
            when version_number = 1 then cast('1900-01-01' as timestamp)
            else dbt_valid_from
        end as valid_from_ts,
        coalesce(dbt_valid_to, cast('9999-12-31' as timestamp)) as valid_to_ts,
        (dbt_valid_to is null) as is_current
    from snapshotted
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'", "cast('1900-01-01' as timestamp)"]) }} as product_key,
        -1 as product_id,
        'Unknown' as product_name,
        'Unknown' as category,
        'Unknown' as brand,
        cast(0 as number(12, 2)) as list_price,
        'unknown' as list_price_band,
        cast('1900-01-01' as timestamp) as valid_from_ts,
        cast('9999-12-31' as timestamp) as valid_to_ts,
        true as is_current
)

select * from versions
union all
select * from unknown_member
