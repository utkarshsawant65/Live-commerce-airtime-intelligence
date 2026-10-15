{# Shows, deduped on show_id (SCD1: latest load wins). #}

with source as (
    select * from {{ source('raw', 'dim_show') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by show_id
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

select
    show_id,
    trim(show_name) as show_name,
    lower(trim(show_format)) as show_format,
    loaded_at_utc
from deduped
where _row_num = 1
