/* ============================================================================
   Dynamic table: the declarative alternative to Streams + Tasks. Snowflake keeps
   marts.dt_order_line within TARGET_LAG of raw.fact_order_line, refreshing
   incrementally where it can. Prefer Streams + Tasks when the logic needs multiple
   statements or explicit control of the MERGE.

   Prerequisites: role_transformer needs SELECT on the source (granted in
   00_setup.sql) and CREATE DYNAMIC TABLE on MARTS (included in ALL on the schema).
   Change tracking must be on for the source table, enabled by its owner:
     use role role_loader;
     alter table live_commerce.raw.fact_order_line set change_tracking = true;
   ============================================================================ */

use role role_transformer;
use database live_commerce;
use warehouse wh_transform;

-- A dynamic table starts refreshing as soon as it is created.
create dynamic table if not exists marts.dt_order_line
    target_lag = '1 minute'
    warehouse = wh_transform
    comment = 'Order lines kept within one minute of raw.fact_order_line.'
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

-- Suspended straight away so it does not consume credits until resumed.
alter dynamic table marts.dt_order_line suspend;

-- Resume / suspend:
-- alter dynamic table marts.dt_order_line resume;
-- alter dynamic table marts.dt_order_line suspend;

-- Lag and refresh history:
-- show dynamic tables like 'DT_ORDER_LINE' in schema marts;
-- select * from table(information_schema.dynamic_table_refresh_history(
--     name => 'LIVE_COMMERCE.MARTS.DT_ORDER_LINE')) order by data_timestamp desc;
