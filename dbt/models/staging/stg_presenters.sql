{# Presenters, deduped on presenter_id (SCD1: latest load wins). #}

with source as (
    select * from {{ source('raw', 'dim_presenter') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by presenter_id
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

select
    presenter_id,
    trim(presenter_name) as presenter_name,
    lower(trim(experience_tier)) as experience_tier,
    loaded_at_utc
from deduped
where _row_num = 1
