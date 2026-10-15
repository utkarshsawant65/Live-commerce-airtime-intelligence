# Near-real-time option

The default pipeline is micro-batch: the generator's files are `PUT` to a stage, loaded with
`COPY INTO`, and dbt rebuilds the models (incrementally for `fact_order_line`). The scripts
here show how to process order lines continuously inside Snowflake if fresher data were
needed. They are optional and not part of the dbt build.

## Why micro-batch is the default

The consumers are dashboards and segment-level KPIs, where minutes of latency are fine. A
message bus such as Kafka would add a second system to run, and the data would still be
loaded into the warehouse in batches. If fresher data is required, Snowflake's own features
cover it:

| Option | File | Use when |
|---|---|---|
| Stream + Task | [`01_streams_and_tasks.sql`](01_streams_and_tasks.sql) | the logic needs explicit `MERGE` control or several statements |
| Dynamic Table | [`02_dynamic_tables.sql`](02_dynamic_tables.sql) | the transform is a single `SELECT` and a target lag is enough |

Continuous ingestion (rather than continuous transformation) would be the next step:
Snowpipe auto-ingest or Snowpipe Streaming.

## What the scripts create

- **Stream + Task:** a stream on `raw.fact_order_line` and a task that runs every minute,
  only when the stream has data, merging new rows into `marts.rt_order_line`. Tasks are created
  suspended; `alter task raw.task_merge_order_line resume;` starts it.
- **Dynamic Table:** `marts.dt_order_line` with `target_lag = '1 minute'` over
  `raw.fact_order_line`. It is suspended right after creation;
  `alter dynamic table marts.dt_order_line resume;` starts refreshes.

## Prerequisites

Run `snowflake/00_setup.sql` and load at least one batch first. Each script lists its
prerequisites in its header:

- Both: change tracking on `raw.fact_order_line`, enabled by its owner (`role_loader`).
- Streams + Tasks: `CREATE STREAM` / `CREATE TASK` on `RAW` and `EXECUTE TASK` on the account
  for `role_transformer`, granted as ACCOUNTADMIN. These are not in `00_setup.sql`.
- Dynamic Table: otherwise only what `00_setup.sql` grants (`SELECT` on RAW, `ALL` on
  `MARTS`).

Both scripts use `create ... if not exists` and can be run from a Snowsight worksheet.
