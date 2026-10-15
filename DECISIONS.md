# Architecture Decisions & Trade-offs

Every non-trivial choice in this project, the alternative we rejected, the one-sentence
defense, and an honest **defensibility rating**. The rule: *if two approaches exist and we
picked one, we must be able to defend it — or change it.* Where a decision is only
medium-strength, the risk is stated plainly rather than hidden.

Rating key: **Strong** = holds against a hardened reviewer · **Medium** = defensible but has a
real counter-argument to acknowledge · **Conditional** = correct only given stated assumptions.

---

## Ingestion & real-time

### D1 — Micro-batch ingestion, not Kafka/Pub-Sub — **Strong**
- **Chosen:** `PUT` + `COPY INTO` on a cadence; dbt incremental re-models the delta.
- **Rejected:** Kafka / Pub-Sub streaming tier.
- **Defense:** This is an analytical warehouse workload (minutes-fresh KPIs), not an
  operational one (sub-second events). Kafka's latency advantage is discarded at the
  warehouse boundary — you still land in micro-batches. A streaming bus here is a second
  distributed system to operate for no analytical benefit.
- **Upgrade path (built, not used):** Snowflake-native Streams + Tasks / Dynamic Tables, then
  Snowpipe Streaming. See [`streaming/`](streaming/). This is the *correct* "more real-time"
  answer and reaching for Kafka first would signal less maturity, not more.

### D2 — Land semi-structured feed as VARIANT (schema-on-read) — **Strong**
- **Chosen:** `raw.order_line_raw` holds the JSON payload as one `VARIANT` column; parsing,
  type-casting, and validation happen in staging.
- **Rejected:** typed RAW table that the source must match exactly.
- **Defense:** a typed landing table breaks the instant a source adds/renames a field or sends
  a bad type. VARIANT decouples ingestion from schema, so a source change never breaks the
  load — failures become observable rows we quarantine, not a red pipeline.

### D3 — `ON_ERROR` configurable; bad rows quarantined, not dropped — **Strong**
- **Chosen:** CSV loads default to `abort_statement` (strict typed feeds); the VARIANT feed
  uses `continue`; the VARIANT staging splits valid rows from a **quarantine** model with a
  failure reason per row.
- **Rejected:** assume every row loads; silently drop or hard-fail on bad rows.
- **Defense:** real multi-source feeds are dirty. Capturing bad rows with a reason makes data
  quality observable and backfillable; a singular test proves no "valid"-flagged row has null
  keys, so dirt cannot leak downstream.

---

## Dimensional modelling

### D4 — SCD2 only on `dim_product`; SCD1 elsewhere — **Strong**
- **Chosen:** Type 2 history on product (category, price band); Type 1 (overwrite) on
  presenter/show; static channel.
- **Rejected:** historise everything "just in case."
- **Defense:** Type 2 earns its cost only where point-in-time truth matters — a sale must be
  measured against the product's category/price *as it aired*. Presenter/show attributes don't
  change meaningfully over the window. Over-historising is cost, complexity, and a judgment
  red flag.

### D5 — Transaction price in the FACT, not the dimension — **Strong**
- **Chosen:** `unit_price_paid` / monetary measures live on `fact_order_line`.
- **Rejected:** modelling paid price as a product attribute.
- **Defense:** the price paid is a measure of the event, not a slowly-changing attribute of the
  product. Putting it in the dimension is a textbook dimensional-modelling error.

### D6 — Incremental only on `fact_order_line` — **Strong**
- **Chosen:** the one high-volume fact is `incremental` (merge + lookback); everything else is
  `table`/`view`.
- **Rejected:** incremental dimensions / all facts.
- **Defense:** incremental earns its complexity only at volume. Small dims rebuild in seconds
  and are safer fully refreshed; incremental dims add bugs for no gain.

### D7 — Unknown member (-1) in every dimension — **Strong**
- **Chosen:** each dim carries a `-1` row; orphan/late facts join to it.
- **Rejected:** inner joins that silently drop unmatched facts.
- **Defense:** late-arriving dimensions and organic (no-segment) orders are real. The unknown
  member keeps every fact row, and a `relationships` test surfaces genuine orphans for backfill.

---

## Attribution

### D8 — Last-touch attribution within a lag window — **Medium**
- **Chosen:** order → the segment for that product whose `[start, end+lag]` window contains the
  order time; ties → most recent start (last-touch).
- **Rejected:** first-touch; fractional/multi-touch attribution.
- **Defense:** for "what drove this order," the segment the customer most recently saw is the
  most plausible single cause; last-touch is simple, explainable, and testable.
- **Honest counter:** multi-touch is arguably more "correct" when a product airs repeatedly.
  We chose explainability over sophistication and documented the rule so it's auditable, not
  hidden. The lag window is a parameter (`attribution_lag_minutes`), not a magic number.

### D9 — Aggregate (population) conversion lift, not per-product — **Medium**
- **Chosen:** conversion lift = on-air vs off-air at the population level.
- **Rejected:** per-product conversion lift.
- **Defense:** web_sessions cannot be cleanly attributed to a single product per minute, so a
  per-product rate would divide a product's orders by a denominator that isn't really its own.
  The aggregate rate is the honest denominator and is exactly what the z-test consumes.
- **Honest counter:** the headline lift is therefore population-level; per-product lift would
  need a per-product session model we deliberately did not build (scope/honesty trade-off).

---

## Semantic layer & BI

### D10 — dbt-core single metrics mart, not a paid Semantic Layer — **Strong**
- **Chosen:** one governed `mart_kpi_metrics` (+ catalog) defines each KPI once.
- **Rejected:** dbt Cloud / MetricFlow Semantic Layer.
- **Defense:** a modelled metrics mart + documented catalog delivers "consistent KPIs" with
  dbt-core alone; the paid layer is the productionised version of the same idea, mentioned but
  not required.

### D11 — Power BI Import for the demo — **Conditional**
- **Chosen:** Import mode.
- **Rejected:** DirectQuery / Composite.
- **Defense:** Import is fast, reliable, and survives a trial expiry as a cached model.
- **Assumption:** demo, not production. Stated upgrade: scheduled/incremental refresh or a
  Composite model (Import dims + DirectQuery hot facts) in a Service deployment.

### D12 — One BI tool (Power BI), not two — **Strong**
- **Defense:** Power BI / Tableau / Looker are substitutes for the same job. Depth in one (with
  RLS, governance, a clean star model) beats two shallow dashboards solving the same problem.

---

## Statistics

### D13 — Exactly one test: a two-proportion z-test — **Strong**
- **Chosen:** one correctly-applied test for "is the on-air conversion lift significant?"
- **Rejected:** a battery of tests; ML/forecasting.
- **Defense:** the data is two groups of Bernoulli trials summarised as counts + denominators —
  the textbook setting for comparing two proportions. We report the statistic, p-value, and a
  plain-language conclusion, and state assumptions honestly (sessions as independent trials,
  population-level not per-user causal). One correct test beats an over-claimed stats zoo.

---

## Platform & engineering

### D14 — Separated warehouses (LOAD / TRANSFORM / BI) — **Strong**
- **Defense:** isolates workloads (no contention) and makes spend attributable per workload —
  a performance *and* cost-governance story. `AUTO_SUSPEND` caps idle cost.

### D15 — Functional RBAC + clean schema names — **Strong**
- **Defense:** access by job function, enforced at the warehouse layer; Power BI RLS enforces
  again at the report layer (defence in depth). Clean schema names keep grants and connection
  strings identical across environments.

### D16 — Synthetic data — **Medium**
- **Chosen:** generate the data with a known airtime→sales correlation.
- **Defense:** no public dataset has a broadcast/airtime dimension; generating it gives ground
  truth, so the attribution and the lift test can be *verified* against a known answer.
- **Honest counter:** synthetic data is cleaner and more separable than reality — which is
  exactly why we added a `--dirty` mode (nulls, dupes, bad types, orphans, out-of-order rows)
  and a quarantine pipeline, so robustness is demonstrated, not assumed.

---

## Open items / would-do-next (stated, not hidden)

- Per-product conversion lift would need a per-minute product-of-record session model.
- Multi-touch attribution as an alternative to last-touch, if repeated airings dominate.
- Snowpipe Streaming for true continuous ingestion if a seconds-fresh requirement appears.
- Power BI deployment pipelines / incremental refresh (needs Premium/Fabric).
