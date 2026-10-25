{# A channel airs one segment at a time: no two segments on a channel overlap in [start, end). #}

select
    a.channel_id,
    a.segment_id as segment_a,
    b.segment_id as segment_b
from {{ ref('stg_airtime_segments') }} as a
inner join {{ ref('stg_airtime_segments') }} as b
    on
        a.channel_id = b.channel_id
        and a.segment_id < b.segment_id
        and a.start_ts_utc < b.end_ts_utc
        and a.end_ts_utc > b.start_ts_utc
