/* ============================================================================
   Streams + Tasks: continuous incremental processing of raw.fact_order_line.
   A stream records rows added since it was last consumed; a task merges that
   delta into marts.rt_order_line every minute, only when the stream has data.

   Prerequisites (run once as ACCOUNTADMIN):
     grant create stream, create task on schema live_commerce.raw to role role_transformer;
     grant execute task on account to role role_transformer;
   Change tracking must be on for the source table, and only its owner (role_loader)
   can enable it:
     use role role_loader;
     alter table live_commerce.raw.fact_order_line set change_tracking = true;
   ============================================================================ */

use role role_transformer;
use database live_commerce;
use warehouse wh_transform;

create stream if not exists raw.strm_order_line
    on table raw.fact_order_line
    comment = 'Order-line rows added or changed since last consumption.';

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

-- The WHEN clause skips the run (no warehouse time) when the stream is empty.
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
        -- Updates appear as a DELETE + INSERT pair; the INSERT carries the new values.
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

-- Tasks are created suspended. Resume to start the schedule; suspend to stop it.
-- alter task raw.task_merge_order_line resume;
-- alter task raw.task_merge_order_line suspend;

-- Recent task runs:
-- select * from table(information_schema.task_history(
--     task_name => 'TASK_MERGE_ORDER_LINE')) order by scheduled_time desc;
