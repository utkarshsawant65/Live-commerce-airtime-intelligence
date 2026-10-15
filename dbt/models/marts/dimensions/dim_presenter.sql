{#
    dim_presenter — conformed presenter dimension, SCD Type 1 (overwrite).
    Presenter attributes don't meaningfully change over the analysis window, so Type 1
    is correct; Type 2 here would be over-engineering.
    Includes the unknown member (-1) for orphan/late facts.
#}

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
