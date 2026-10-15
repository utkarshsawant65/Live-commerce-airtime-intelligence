{# Airtime segments, deduped on segment_id. Timestamps are UTC (TIMESTAMP_NTZ). #}

with source as (
    select * from {{ source('raw', 'fact_airtime_segment') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by segment_id
            order by loaded_at_utc desc, end_ts_utc desc
        ) as _row_num
    from source
)

select
    segment_id,
    product_id,
    presenter_id,
    show_id,
    channel_id,
    start_ts_utc,
    end_ts_utc,
    segment_duration_minutes,
    loaded_at_utc
from deduped
where _row_num = 1
