# Design Decisions

The main design choices, the alternative considered, and why. Each is rated:

- **Strong:** few real counter-arguments for this workload.
- **Medium:** a reasonable choice with a real trade-off, stated below.
- **Conditional:** right only under the stated assumption.

---

## Ingestion

### D1. Micro-batch ingestion, not Kafka / Pub-Sub (Strong)
- **Chosen:** `PUT` + `COPY INTO` on a cadence; dbt reprocesses the delta incrementally.
- **Rejected:** a streaming bus in front of the warehouse.
- **Why:** this is an analytical workload where minutes-fresh KPIs are enough. Data still
  lands in the warehouse in batches, so a bus would add a second system to operate without
  improving the reports.
- **If fresher data is needed:** Snowflake Streams + Tasks or Dynamic Tables, then Snowpipe
  Streaming. Scripts are in [`streaming/`](streaming/).

### D2. Land the JSON feed as VARIANT (Strong)
- **Chosen:** `raw.order_line_raw` holds each payload in one `VARIANT` column; parsing, casting
  and validation happen in `stg_order_lines_variant`.
- **Rejected:** a typed landing table the source must match exactly.
- **Why:** a typed table fails the load when a field is renamed or a value has the wrong type.
  With VARIANT the load always succeeds and bad rows become visible in staging.

### D3. Bad rows quarantined, not dropped (Strong)
- **Chosen:** typed CSV loads default to `ON_ERROR = abort_statement` (`--on-error` overrides);
  the VARIANT load uses `continue`. `stg_order_lines_variant` flags each row valid or invalid
  with a reason, and `stg_order_lines_quarantine` keeps the invalid ones.
- **Rejected:** assuming every row is clean, or silently dropping bad rows.
- **Why:** bad rows stay inspectable, and a singular test checks that no row marked valid has
  missing or out-of-range keys.

---

## Dimensional modelling

### D4. SCD2 only on product (Strong)
- **Chosen:** a dbt snapshot (`check` strategy on category, price band, list price, brand and
  name) over the current product row. A new version is effective from the snapshot run that
  captured the change, i.e. processing time. `dim_product` opens each product's first version
  at 1900-01-01 so events before the first snapshot still resolve. Presenter and show are SCD1.
- **Rejected:** historising every dimension.
- **Why:** category and price band are the attributes that change and that sales are analysed
  by. Presenter and show attributes do not change meaningfully here, so history would be cost
  without use.
- **Trade-off:** versions follow snapshot time, not a source effective date. A change is
  attributed correctly only if snapshots run soon after it lands.
  [`snowflake/demo_scd2.sql`](snowflake/demo_scd2.sql) shows a change being captured.

### D5. Transaction price on the fact (Strong)
- **Chosen:** `unit_price_paid` and the money measures live on `fact_order_line`.
- **Rejected:** treating paid price as a product attribute.
- **Why:** the price paid belongs to the sale, not to the product.

### D6. Incremental only on `fact_order_line` (Strong)
- **Chosen:** `merge` on `order_line_id`. Each run reprocesses rows within
  `incremental_lookback_minutes` (120) of the latest event plus any row loaded since the last
  run (`loaded_at_utc`), so late-arriving rows are picked up. Everything else is a table or view.
- **Rejected:** incremental dimensions or incremental on every fact.
- **Why:** order lines are the only high-volume table. The rest rebuild in seconds and are
  simpler to keep correct with full refreshes.

### D7. Unknown member in the entity dimensions (Strong)
- **Chosen:** product, presenter, show and channel each have an unknown row (`-1`, named
  `Unknown`); orphan facts join to it. Date and time-of-day are generated and need none.
- **Rejected:** inner joins that drop unmatched facts.
- **Why:** organic orders and late dimension rows are expected. Facts are kept, and
  `relationships` tests surface real orphans.

---

## Attribution

### D8. Last-touch attribution within a lag window (Medium)
- **Chosen:** an order line goes to the segment for the same product whose window
  `[start, end + attribution_lag_minutes)` contains the order time; ties go to the latest start.
  No match means organic (`-1`).
- **Rejected:** first-touch, or fractional multi-touch.
- **Why:** the most recent airing is the most plausible single cause, and the rule is simple
  to explain and test.
- **Trade-off:** when a product airs repeatedly, multi-touch could be more accurate. The lag
  is a dbt var (30 min) that must match `LAG_MINUTES` in the generator and the z-test.

### D9. Population-level conversion lift (Medium)
- **Chosen:** conversion = distinct online orders ÷ online web sessions, split by whether the
  minute falls in any segment's window. The mart is sliceable by date, channel and daypart.
- **Rejected:** per-product conversion lift.
- **Why:** sessions are not tied to a product, so a per-product rate would divide by a
  denominator that is not really the product's. Using one on-air rule for orders and sessions
  keeps numerator and denominator consistent.
- **Trade-off:** the lift is about broadcast time overall, not a specific product. This is
  separate from `is_airtime_attributed`, which drives attributed revenue.

---

## Semantic layer and BI

### D10. Metrics mart in dbt-core, not a paid semantic layer (Strong)
- **Chosen:** `mart_kpi_metrics` plus [`docs/KPI_CATALOG.md`](docs/KPI_CATALOG.md) define each
  KPI once.
- **Rejected:** dbt Cloud Semantic Layer / MetricFlow.
- **Why:** a tested mart and a catalog give consistent KPIs with dbt-core alone.

### D11. Power BI Import mode (Conditional)
- **Chosen:** Import.
- **Rejected:** DirectQuery or composite models.
- **Why:** fast, and the report keeps working from its cached model.
- **Assumption:** a report over a fixed dataset. In a hosted deployment I would use scheduled
  or incremental refresh, or a composite model with DirectQuery on the hot facts.

### D12. One BI tool (Strong)
- **Why:** Power BI, Tableau and Looker do the same job here. One report with a clean star
  model and RLS is more useful than the same charts in two tools.

---

## Statistics

### D13. One test: a two-proportion z-test (Strong)
- **Chosen:** a one-sided z-test of on-air vs off-air conversion, using only the standard
  library (`math.erfc`).
- **Rejected:** several tests, or a forecasting model.
- **Why:** the data is two groups of trials summarised as counts and denominators, which is
  the standard setting for comparing two proportions. Assumptions: sessions are treated as
  independent trials, and the result is population-level, not a per-user causal estimate.

---

## Platform

### D14. Separate warehouses for load, transform and BI (Strong)
- **Why:** no contention between workloads, spend is attributable per workload, and
  `AUTO_SUSPEND = 60` limits idle cost.

### D15. Functional roles and unprefixed schema names (Strong)
- **Why:** `role_loader`, `role_transformer` and `role_bi_reader` each get only what their job
  needs (BI reads `MARTS` only); Power BI RLS restricts rows again in the report. Schema names
  are the same in every environment, so grants and connections don't change. CI adds a `_CI`
  suffix via `DBT_SCHEMA_SUFFIX`.

### D16. Synthetic data (Medium)
- **Chosen:** generate data with a known airtime effect.
- **Why:** no public dataset has an airtime dimension, and a known effect means the
  attribution and lift test can be checked.
- **Trade-off:** synthetic data is cleaner than real feeds. `--dirty` corrupts the JSON
  order-line feed (duplicates, null product ids, non-numeric quantities, orphan product ids,
  late timestamps) and writes a manifest of what it injected, to exercise the VARIANT path.

---

## Known limitations / next steps

- The VARIANT feed is a parallel landing path: the facts are built from the typed CSV feed,
  so dirty-mode rows are visible in `stg_order_lines_variant` and the quarantine but do not
  reach `fact_order_line`.
- SCD2 versions use snapshot (processing) time rather than a source effective date.
- CI truncates and reloads the unsuffixed RAW schema; it should use its own database or account.
- Per-product conversion lift would need a session-to-product model.
- Multi-touch attribution as an alternative to last-touch, if repeated airings dominate.
- Snowpipe Streaming if a seconds-fresh requirement appears.
- Power BI deployment pipelines and incremental refresh (needs Premium/Fabric).
- Publish `dbt docs` to GitHub Pages.
