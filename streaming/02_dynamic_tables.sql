/* ============================================================================
   Production-readiness showcase (2 of 2): Dynamic Tables
   The declarative alternative to Streams + Tasks.

   A DYNAMIC TABLE is defined by a SELECT plus a TARGET_LAG. Snowflake keeps it
   refreshed within that lag, incrementally, automatically — no stream to manage, no
   task to schedule, no MERGE to hand-write. You declare the result and the freshness
   you want; the platform figures out the incremental maintenance.

   When to prefer this over Streams + Tasks:
     * You want declarative freshness (TARGET_LAG) instead of imperative scheduling.
     * The transform is expressible as a single SELECT (most modelling is).
   When to prefer Streams + Tasks:
     * You need procedural/multi-statement logic, calls, or fine control of the MERGE.

   Run as ROLE_TRANSFORMER.
   ============================================================================ */

use role role_transformer;
use database live_commerce;
use warehouse wh_transform;

-- A near-real-time attributed-order table, declared once. Snowflake maintains it
-- within ~1 minute of the upstream raw.fact_order_line changing — incrementally.
create dynamic table if not exists marts.dt_order_line
    target_lag = '1 minute'
    warehouse = wh_transform
    comment = 'Declarative near-real-time order lines; maintained incrementally by Snowflake.'
as
    select
        order_line_id,
        order_id,
        product_id,
        channel_id,
        event_ts_utc,
        quantity,
        net_amount
    from raw.fact_order_line;

-- Freshness/lag and refresh history:
-- show dynamic tables like 'DT_ORDER_LINE' in schema marts;
-- select * from table(information_schema.dynamic_table_refresh_history(
--     name => 'LIVE_COMMERCE.MARTS.DT_ORDER_LINE')) order by data_timestamp desc;

-- Suspend/resume to control cost:
-- alter dynamic table marts.dt_order_line suspend;
-- alter dynamic table marts.dt_order_line resume;
