{# Every attributed order falls inside its segment's window [start_ts_utc, end_ts_utc + lag). #}

select
    f.order_line_id,
    f.event_ts_utc,
    s.start_ts_utc,
    s.end_ts_utc
from {{ ref('fact_order_line') }} as f
inner join {{ ref('fact_airtime_segment') }} as s
    on f.attributed_segment_id = s.segment_id
where
    f.attributed_segment_id != -1
    and (
        f.event_ts_utc < s.start_ts_utc
        or f.event_ts_utc >= dateadd('minute', {{ var('attribution_lag_minutes') }}, s.end_ts_utc)
    )
