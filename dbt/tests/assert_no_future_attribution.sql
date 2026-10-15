{#
    Singular test: no order may be attributed to an airtime
    segment that started AFTER the order was placed. A real attribution must point at a
    segment whose window the order falls within — never a future broadcast.

    Returns offending rows; the test passes when it returns zero rows.

    This is the test-first guardrail on the attribution model's core invariant: causality
    runs one way (air -> buy), never backwards.
#}

select
    f.order_line_id,
    f.attributed_segment_id,
    f.event_ts_utc,
    s.start_ts_utc as segment_start_ts
from {{ ref('fact_order_line') }} as f
inner join {{ ref('fact_airtime_segment') }} as s
    on f.attributed_segment_id = s.segment_id
-- An attributed segment that starts after the order event is a logic error.
where s.start_ts_utc > f.event_ts_utc
