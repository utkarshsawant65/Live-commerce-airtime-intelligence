/* ============================================================================
   Production-readiness showcase (1 of 2): Streams + Tasks
   Snowflake-native continuous incremental processing.

   This module is NOT used by the demo (the demo runs on micro-batch COPY INTO +
   dbt incremental, which is correct for an analytical warehouse). It exists to show
   the production upgrade path: if the business needed near-real-time freshness, this
   is how we would do it ON SNOWFLAKE — no second system (no Kafka), no extra ops.

   How it works:
     * A STREAM on raw.fact_order_line records the change set (new rows) since it was
       last consumed — change-data-capture, built into the table.
     * A scheduled TASK wakes on a cadence (or on a stream-has-data condition), reads
       only the stream's delta, and MERGEs it into a curated table. Each run touches
       only what changed, so cost scales with the delta, not the table.

   Run as ROLE_TRANSFORMER (needs CREATE STREAM / CREATE TASK on the schemas).
   ============================================================================ */

use role role_transformer;
use database live_commerce;
use warehouse wh_transform;

-- ----------------------------------------------------------------------------
-- 1. A stream = built-in change capture over the raw order-line landing table.
-- ----------------------------------------------------------------------------
create stream if not exists raw.strm_order_line
    on table raw.fact_order_line
    comment = 'CDC stream: new/changed order-line rows since last consumption.';

-- Curated near-real-time target the task maintains incrementally.
create table if not exists marts.rt_order_line (
    order_line_id integer,
    order_id integer,
    product_id integer,
    channel_id integer,
    event_ts_utc timestamp_ntz,
    quantity integer,
    net_amount number(12, 2),
    merged_at_utc timestamp_ntz
);

-- ----------------------------------------------------------------------------
-- 2. A task that consumes the stream delta and MERGEs it — only when there is data.
--    SCHEDULE could be '1 minute' for near-real-time; we gate on the stream so an
--    idle feed costs nothing (the task no-ops when system$stream_has_data is false).
-- ----------------------------------------------------------------------------
create task if not exists raw.task_merge_order_line
    warehouse = wh_transform
    schedule = '1 minute'
    when system$stream_has_data('raw.strm_order_line')
as
    merge into marts.rt_order_line as tgt
    using (
        select
            order_line_id,
            order_id,
            product_id,
            channel_id,
            event_ts_utc,
            quantity,
            net_amount
        from raw.strm_order_line
        -- Consume only true row inserts/updates from the CDC stream.
        where metadata$action = 'INSERT'
    ) as src
    on tgt.order_line_id = src.order_line_id
    when matched then update set
        tgt.order_id = src.order_id,
        tgt.product_id = src.product_id,
        tgt.channel_id = src.channel_id,
        tgt.event_ts_utc = src.event_ts_utc,
        tgt.quantity = src.quantity,
        tgt.net_amount = src.net_amount,
        tgt.merged_at_utc = sysdate()
    when not matched then insert (
        order_line_id, order_id, product_id, channel_id,
        event_ts_utc, quantity, net_amount, merged_at_utc
    ) values (
        src.order_line_id, src.order_id, src.product_id, src.channel_id,
        src.event_ts_utc, src.quantity, src.net_amount, sysdate()
    );

-- Tasks are created SUSPENDED. Resume to start the schedule; suspend to stop billing.
-- alter task raw.task_merge_order_line resume;
-- alter task raw.task_merge_order_line suspend;

-- Inspect recent task runs:
-- select * from table(information_schema.task_history(
--     task_name => 'TASK_MERGE_ORDER_LINE')) order by scheduled_time desc;
