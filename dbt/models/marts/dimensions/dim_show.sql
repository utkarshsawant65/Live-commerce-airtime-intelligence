{# Show dimension, SCD1 (overwrite), plus the unknown member (-1). #}

with shows as (
    select
        {{ surrogate_key(['show_id']) }} as show_key,
        show_id,
        show_name,
        show_format
    from {{ ref('stg_shows') }}
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'"]) }} as show_key,
        -1 as show_id,
        'Unknown' as show_name,
        'unknown' as show_format
)

select * from shows
union all
select * from unknown_member
