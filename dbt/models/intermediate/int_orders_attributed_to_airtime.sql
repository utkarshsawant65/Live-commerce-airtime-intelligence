{#
    int_orders_attributed_to_airtime — the attribution model.
    THE HEART OF THE PROJECT. For each order line, find the airtime segment that drove
    it, or tag it as organic/baseline (unknown segment -1) if none matches.

    RULES (each is explicit and documented so the attribution is auditable):

    1. Candidate match: a segment for THE SAME PRODUCT whose window
       [start_ts, end_ts + lag_minutes] contains the order's event_ts_utc.
       The lag (var attribution_lag_minutes, default 30) absorbs the realistic delay
       between "seen on air" and "order placed / synced". It MUST equal the generator's
       LAG_MINUTES so the synthetic correlation and the model agree.

    2. Overlap / back-to-back rule: a product can appear in overlapping or consecutive
       windows. We attribute to the segment ACTIVE AT ORDER TIME FEATURING THAT EXACT
       PRODUCT. If more than one qualifies, ties break to the MOST RECENT START
       (last-touch) — the segment the customer most plausibly just saw.

    3. Unknown member: orders with no qualifying segment attribute to segment_id = -1
       (organic / baseline). This is what makes "conversion lift vs baseline" computable
       and ensures no order is silently dropped.

    4. UTC everywhere: all timestamps are UTC; we convert only at the BI layer.

    Materialized ephemeral (see dbt_project.yml) — it is glue between staging and the
    fact, not something BI queries directly.
#}

with orders as (
    select
        order_line_id,
        order_id,
        product_id,
        channel_id,
        event_ts_utc,
        quantity,
        unit_price_paid,
        gross_amount,
        discount_amount,
        net_amount
    from {{ ref('stg_order_lines') }}
),

segments as (
    select
        segment_id,
        product_id,
        presenter_id,
        show_id,
        channel_id as broadcast_channel_id,
        start_ts_utc,
        end_ts_utc,
        -- The attributable window extends past the segment end by the lag.
        dateadd('minute', {{ var('attribution_lag_minutes') }}, end_ts_utc) as attributable_until_ts,
        segment_duration_minutes
    from {{ ref('stg_airtime_segments') }}
),

-- Every (order, candidate segment) pair: same product, order time inside the window.
candidates as (
    select
        o.order_line_id,
        s.segment_id,
        s.start_ts_utc,
        -- Rank candidates per order: most recent start wins (last-touch tie-break).
        row_number() over (
            partition by o.order_line_id
            order by s.start_ts_utc desc, s.segment_id desc
        ) as candidate_rank
    from orders as o
    inner join segments as s
        on o.product_id = s.product_id
        and o.event_ts_utc >= s.start_ts_utc
        and o.event_ts_utc < s.attributable_until_ts
),

best_match as (
    select
        order_line_id,
        segment_id
    from candidates
    where candidate_rank = 1
)

select
    o.order_line_id,
    o.order_id,
    o.product_id,
    o.channel_id,
    o.event_ts_utc,
    o.quantity,
    o.unit_price_paid,
    o.gross_amount,
    o.discount_amount,
    o.net_amount,
    -- Unknown segment (-1) when no airtime window matched = organic / baseline.
    coalesce(b.segment_id, -1) as attributed_segment_id,
    (b.segment_id is not null) as is_airtime_attributed
from orders as o
left join best_match as b
    on o.order_line_id = b.order_line_id
