{#
    dim_channel — conformed channel dimension, static (from the governed seed).
    Includes the unknown member (-1) for orphan/late facts.
#}

with channels as (
    select
        {{ surrogate_key(['channel_id']) }} as channel_key,
        channel_id,
        channel_name,
        channel_type
    from {{ ref('stg_channels') }}
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'"]) }} as channel_key,
        -1 as channel_id,
        'Unknown' as channel_name,
        'unknown' as channel_type
)

select * from channels
union all
select * from unknown_member
