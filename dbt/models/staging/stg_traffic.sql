{#
    Staging: minute-level traffic. 1:1, typed, deduped on (channel_id, minute_ts_utc).
    The conversion denominator — web_sessions feeds Conversion Rate.
#}

with source as (
    select * from {{ source('raw', 'fact_traffic') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by channel_id, minute_ts_utc
            order by loaded_at_utc desc
        ) as _row_num
    from source
)

select
    channel_id,
    minute_ts_utc,
    concurrent_viewers,
    web_sessions,
    loaded_at_utc
from deduped
where _row_num = 1
