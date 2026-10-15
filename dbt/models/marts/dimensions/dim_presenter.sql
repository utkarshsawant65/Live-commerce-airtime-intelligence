{# Presenter dimension, SCD1 (overwrite), plus the unknown member (-1). #}

with presenters as (
    select
        {{ surrogate_key(['presenter_id']) }} as presenter_key,
        presenter_id,
        presenter_name,
        experience_tier
    from {{ ref('stg_presenters') }}
),

unknown_member as (
    select
        {{ surrogate_key(["'-1'"]) }} as presenter_key,
        -1 as presenter_id,
        'Unknown' as presenter_name,
        'unknown' as experience_tier
)

select * from presenters
union all
select * from unknown_member
