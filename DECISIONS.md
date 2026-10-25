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

### D4. SCD2 only on product, dated by event time (Strong)
- **Chosen:** `dim_product` has one row per source version (`product_id` + `product_version`),
  valid over `[valid_from, valid_to)` as dated by the source. Facts join to the version valid
  at their event time, and the singular test
  `assert_order_lines_use_version_valid_at_event_time` checks that every order line does.
  The `dim_product_snapshot` snapshot (`check` strategy) is kept as a capture-time audit trail
  of what the warehouse saw and when. Presenter and show are SCD1.
- **Rejected:** building `dim_product` from the snapshot, and historising every dimension.
- **Why:** a snapshot dates a change by the run that captured it, so a price change that
  lands late would be applied to orders from after the capture, not from when it took effect.
  The source's own validity dates are the effective dates. Category and price are the
  attributes that change and that sales are analysed by; presenter and show attributes do not
  change meaningfully here, so history would be cost without use.
- **Trade-off:** this relies on the source providing correct validity dates. The snapshot
  remains as an independent record if they turn out wrong.
  [`snowflake/demo_scd2.sql`](snowflake/demo_scd2.sql) walks through the snapshot.

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
  No match means organic (`-1`). The model is a view so that a dbt unit test
  (`attribution_window_boundaries_and_tie_break`, seven hand-made cases) can check the
  boundaries and the tie-break.
- **Rejected:** first-touch, or fractional multi-touch.
- **Why:** the most recent airing is the most plausible single cause, and the rule is simple
  to explain and test.
- **Trade-off:** the window length trades precision for recall. Against the generator's
  ground truth, 30 minutes gives 96.3% precision and 87.7% recall on the sample batch; 60
  minutes gives 94.9% / 94.5% (see [docs/VALIDATION.md](docs/VALIDATION.md)). When a product
  airs repeatedly, multi-touch could be more accurate. The lag is a dbt var (30 min) that
  must match `LAG_MINUTES` in the generator and in `analysis/offline_rules.py`.

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
- **Why:** Power BI, Tableau and Looker do the same job here. One report on a clean star
  model is more useful than the same charts in two tools.

---

## Statistics

### D13. One test: a two-proportion z-test (Strong)
- **Chosen:** a one-sided z-test of on-air vs off-air conversion, using only the standard
  library, with 95% CIs for the absolute and relative lift. `--bootstrap-days` adds a
  day-level bootstrap CI.
- **Rejected:** several tests, or a forecasting model.
- **Why:** the data is two groups of trials summarised as counts and denominators, which is
  the standard setting for comparing two proportions. Assumptions: sessions are treated as
  independent trials, and the result is population-level, not a per-user causal estimate.
  The day-level bootstrap relaxes the independence assumption.

---

## Platform

### D14. Separate warehouses for load, transform and BI (Strong)
- **Why:** no contention between workloads, spend is attributable per workload, and
  `AUTO_SUSPEND = 60` limits idle cost.

### D15. Functional roles and unprefixed schema names (Strong)
- **Why:** `role_loader`, `role_transformer` and `role_bi_reader` each get only what their job
  needs (BI reads `MARTS` only). Schema names are the same in every environment, so grants
  and connections don't change. CI adds a `_CI` suffix via `DBT_SCHEMA_SUFFIX`.

---

## Synthetic data

### D16. Synthetic data with planted parameters (Medium)
- **Chosen:** generate data from named parameters (base conversion, on-air uplift, tier and
  prime-time weights, price factor, return rates, response lag), record them in the batch
  manifest, and write the true segment of every order line to
  `_truth_order_attribution.csv`. The truth file is never loaded into Snowflake (the loader
  refuses `_truth_*` files), so the pipeline measures blind.
  `analysis/validate_against_truth.py` checks parameter recovery, attribution precision and
  recall, a null test (`--onair-uplift 0`) and CI coverage over 50 seeds.
- **Rejected:** a public dataset (none has an airtime dimension), or synthetic data checked
  only by eye.
- **Why:** with known inputs, every measured number has a target, and a wrong rule shows up
  as a recovery failure rather than a plausible-looking chart.
- **Trade-off:** synthetic data is cleaner than real feeds, and recovering parameters that
  the same author planted shows the pipeline is internally consistent, not that the model of
  viewer behaviour is right. `--dirty` corrupts the JSON order-line feed (duplicates, null
  product ids, non-numeric quantities, orphan product ids, late timestamps) and records what
  it injected, to exercise the VARIANT path.

### D17. Orders driven by sessions × conversion (Strong)
- **Chosen:** online orders are generated from sessions. Every online minute converts at
  `BASE_CONVERSION` (1.5%); each segment adds incremental orders at
  `BASE_CONVERSION × ONAIR_UPLIFT × segment weight` per session during its airing, timed by a
  lognormal response lag. The traffic halo raises on-air session volume only.
- **Rejected:** the earlier generator, which drew orders and traffic separately and produced
  the lift through the traffic halo.
- **Why:** in the earlier version orders ÷ sessions had no modelled meaning: the measured lift
  was set by the halo (+449% at halo 1.0, +10% at 5.0) rather than by any conversion
  parameter. Now the conversion rate is a planted parameter the pipeline must recover, and
  the halo check shows the measured lift follows the planted value across halo settings.
- **Trade-off:** the expected lift still depends slightly on the halo, because airtime orders
  arriving after the 30-minute window count in the off-air baseline. The manifest computes
  this expected value per batch, so the comparison stays exact.

---

## Known limitations / next steps

- Snowflake verification comes from a trial account (October 2026). CI runs the Snowflake
  build only while the secrets are configured, and each run reloads the shared RAW schema.
- The Power BI report was built on the earlier generator and needs a refresh from a
  documented dataset (`--days 90 --seed 42`). The `.pbix` is not in the repo.
- Row-level security in the Power BI report is planned; it cannot be verified from the repo
  because the `.pbix` is not included. The access control in the repo is Snowflake RBAC
  (`snowflake/00_setup.sql`).
- Tier and prime-time recovery CIs are wide on 5 days of data (star / established 0.86 to
  1.56); longer batches narrow them.
- The measured lift depends slightly on the halo through late airtime orders (D17).
- Attribution recall is below 100% at 30 minutes by design: the planted lag has a long tail.
- The VARIANT feed is a parallel landing path: the facts are built from the typed CSV feed,
  so dirty-mode rows are visible in `stg_order_lines_variant` and the quarantine but do not
  reach `fact_order_line`.
- CI truncates and reloads the unsuffixed RAW schema; it should use its own database or account.
- Per-product conversion lift would need a session-to-product model.
- Multi-touch attribution as an alternative to last-touch, if repeated airings dominate.
- Snowpipe Streaming if a seconds-fresh requirement appears.
- Power BI deployment pipelines and incremental refresh (needs Premium/Fabric).
- Publish `dbt docs` to GitHub Pages.
