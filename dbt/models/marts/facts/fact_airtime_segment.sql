{#
    One row per broadcast segment. product_key is the dim_product version valid at
    start_ts_utc. Daypart and prime-time flag come from the start hour, using the same
    bands as dim_time_minute.
#}

with segments as (
    select * from {{ ref('stg_airtime_segments') }}
),

dim_product_pit as (
    select
        product_id,
        product_key,
        valid_from_ts,
        valid_to_ts
    from {{ ref('dim_product') }}
),

dim_presenter as (
    select
        presenter_id,
        presenter_key
    from {{ ref('dim_presenter') }}
),

dim_show as (
    select
        show_id,
        show_key
    from {{ ref('dim_show') }}
),

dim_channel as (
    select
        channel_id,
        channel_key
    from {{ ref('dim_channel') }}
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
    {{ is_prime_time('hour(s.start_ts_utc)') }} as is_prime_time,
    {{ daypart('hour(s.start_ts_utc)') }} as daypart,
    s.segment_duration_minutes
from segments as s
left join dim_product_pit as p
    on
        s.product_id = p.product_id
        and s.start_ts_utc >= p.valid_from_ts
        and s.start_ts_utc < p.valid_to_ts
left join dim_presenter as pr on s.presenter_id = pr.presenter_id
left join dim_show as sh on s.show_id = sh.show_id
left join dim_channel as c on s.channel_id = c.channel_id
cross join (
    select product_key from {{ ref('dim_product') }}
    where product_id = -1
) as dpu
cross join (
    select presenter_key from {{ ref('dim_presenter') }}
    where presenter_id = -1
) as dpru
cross join (
    select show_key from {{ ref('dim_show') }}
    where show_id = -1
) as dshu
cross join (
    select channel_key from {{ ref('dim_channel') }}
    where channel_id = -1
) as dcu
