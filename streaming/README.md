# Production-Readiness: Real-Time Ingestion (showcase, not used by the demo)

This module exists to answer one question directly: **"This is batch — how would you make it
real-time, and why isn't it already?"** It is deliberately **not wired into the demo**, because
micro-batch is the correct choice for this workload. It documents and *builds* the upgrade path
so the decision is defensible, not hand-waved.

## TL;DR decision

> The demo ingests via micro-batch (`PUT` + `COPY INTO`) and re-models with dbt incremental.
> That is the industry-standard pattern for an **analytical** warehouse. If the business
> required near-real-time freshness, the correct upgrade is **Snowflake-native** —
> **Streams + Tasks** or **Dynamic Tables** — not a streaming bus like Kafka.

## Why not Kafka / Pub-Sub here?

Kafka and Pub/Sub solve **low-latency, high-throughput event transport** with many producers
and consumers decoupled in real time. They are the right tool for *operational* systems
(fraud scoring, live inventory, clickstream fan-out). This project is the opposite shape:

| Dimension | This workload | What Kafka is for |
|---|---|---|
| Latency need | minutes-fresh analytics is correct | sub-second event processing |
| Consumer | a warehouse queried for KPIs | many real-time microservices |
| Pattern | batch/micro-batch analytical load | continuous event streaming |
| Cost/ops | one platform (Snowflake) | a second distributed system to run |

Putting Kafka *in front of* a warehouse you query for daily/segment KPIs adds a streaming tier
whose latency advantage the warehouse boundary discards — you still land in micro-batches. It
would be **over-engineering**, and a mature reviewer reads it as such. The right "more
real-time" answer stays inside the platform you already chose.

## The two native upgrade paths (both built here)

### 1. Streams + Tasks — [`01_streams_and_tasks.sql`](01_streams_and_tasks.sql)
- A **STREAM** captures the change set (CDC) on `raw.fact_order_line`.
- A scheduled **TASK** consumes only the delta and `MERGE`s it into a curated table.
- Gated on `system$stream_has_data(...)` so an idle feed costs nothing.
- Choose when you need procedural/multi-statement logic or fine control over the merge.

### 2. Dynamic Tables — [`02_dynamic_tables.sql`](02_dynamic_tables.sql)
- Declare a `SELECT` + a `TARGET_LAG`; Snowflake maintains it incrementally within that lag.
- No stream, no task, no hand-written merge — declarative freshness.
- Choose when the transform is a single `SELECT` (most modelling) and you want freshness as a
  declared SLA rather than an imperative schedule.

### The tier beyond these
If ingestion itself had to be continuous (not just the transform), the next step is
**Snowpipe Streaming** (row-level ingestion via the SDK) or **Snowpipe auto-ingest** (file
notifications). Still Snowflake-native; still no second system to operate.

## When I would actually flip the switch

I would move off micro-batch only when a concrete requirement appears:
- A stakeholder needs a KPI fresh in **seconds, not minutes** (e.g. a live on-air ops board).
- The micro-batch cadence can no longer keep up with volume within its window.

Until then, micro-batch is cheaper, simpler, fully testable in CI, and demonstrably correct.

## How to run (when a Snowflake account exists)

```sql
-- after snowflake/00_setup.sql and at least one load:
!source streaming/01_streams_and_tasks.sql      -- then: alter task ... resume;
-- or, the declarative alternative:
!source streaming/02_dynamic_tables.sql
```

Both are idempotent (`create ... if not exists`) and suspended/cost-controlled by default.
