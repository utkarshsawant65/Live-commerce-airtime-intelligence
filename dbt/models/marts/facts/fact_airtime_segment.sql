{#
    fact_airtime_segment — GRAIN: one row per broadcast segment
    (a product featured in one continuous on-air window).
    Measure: segment_duration_minutes (the denominator for revenue-per-broadcast-minute).
    Table materialization (low volume).

    Product FK is point-in-time: the segment is tied to the product version that was
    valid when it aired (start_ts_utc), so revenue-per-minute is measured against the
    product as it was then.
#}

with segments as (
    select * from {{ ref('stg_airtime_segments') }}
),

dim_product_pit as (
    select product_id, product_key, valid_from_ts, valid_to_ts from {{ ref('dim_product') }}
),

dim_presenter as (
    select presenter_id, presenter_key from {{ ref('dim_presenter') }}
),

dim_show as (
    select show_id, show_key from {{ ref('dim_show') }}
),

dim_channel as (
    select channel_id, channel_key from {{ ref('dim_channel') }}
)

select
    s.segment_id,
    coalesce(p.product_key, dpu.product_key) as product_key,
    coalesce(pr.presenter_key, dpru.presenter_key) as presenter_key,
    coalesce(sh.show_key, dshu.show_key) as show_key,
    coalesce(c.channel_key, dcu.channel_key) as channel_key,
    s.start_ts_utc,
    s.end_ts_utc,
    cast(s.start_ts_utc as date) as broadcast_date,
    -- Time-of-day attributes derived from the segment's START hour, so airtime can be
    -- sliced by daypart / prime-time directly (matches dim_time_minute's bands and the
    -- generator's prime-time window of 18:00–22:59).
    case when hour(s.start_ts_utc) between 18 and 22 then true else false end as is_prime_time,
    case
        when hour(s.start_ts_utc) between 5 and 11 then 'morning'
        when hour(s.start_ts_utc) between 12 and 17 then 'afternoon'
        when hour(s.start_ts_utc) between 18 and 22 then 'prime_time'
        else 'late_night'
    end as daypart,
    s.segment_duration_minutes
from segments as s
left join dim_product_pit as p
    on s.product_id = p.product_id
    and s.start_ts_utc >= p.valid_from_ts
    and s.start_ts_utc < p.valid_to_ts
left join dim_presenter as pr on s.presenter_id = pr.presenter_id
left join dim_show as sh on s.show_id = sh.show_id
left join dim_channel as c on s.channel_id = c.channel_id
cross join (select product_key from {{ ref('dim_product') }} where product_id = -1) as dpu
cross join (select presenter_key from {{ ref('dim_presenter') }} where presenter_id = -1) as dpru
cross join (select show_key from {{ ref('dim_show') }} where show_id = -1) as dshu
cross join (select channel_key from {{ ref('dim_channel') }} where channel_id = -1) as dcu
