{# No order is attributed to a segment that started after the order was placed. #}

select
    f.order_line_id,
    f.attributed_segment_id,
    f.event_ts_utc,
    s.start_ts_utc as segment_start_ts
from {{ ref('fact_order_line') }} as f
inner join {{ ref('fact_airtime_segment') }} as s
    on f.attributed_segment_id = s.segment_id
where s.start_ts_utc > f.event_ts_utc
