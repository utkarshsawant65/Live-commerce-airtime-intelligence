{#
    Singular test: every airtime-attributed order must fall inside its segment's
    attributable window [start, end + lag]. This guards the attribution join's core
    rule end-to-end — not just "not in the future" (the other test) but also "not after
    the lag window closed". Catches an off-by-one or a wrong lag var.

    Returns offending rows; passes when zero rows.
#}

select
    f.order_line_id,
    f.event_ts_utc,
    s.start_ts_utc,
    s.end_ts_utc
from {{ ref('fact_order_line') }} as f
inner join {{ ref('fact_airtime_segment') }} as s
    on f.attributed_segment_id = s.segment_id
where f.attributed_segment_id != -1
    and (
        f.event_ts_utc < s.start_ts_utc
        or f.event_ts_utc >= dateadd('minute', {{ var('attribution_lag_minutes') }}, s.end_ts_utc)
    )
