# Project Guide — Live Commerce Airtime Intelligence Platform

The complete reference for understanding and **walking through** this project. It covers the
**business case** (what real problem it solves), **what we built and why**, **how it all
connects and flows**, a **file-by-file index**, the **delivery status** (live on Snowflake,
CI/CD green, SCD2 proven), a **walkthrough guide** (summaries, demo order, design FAQ), and
the **dashboard insights**.

> Scope note: this guide documents every file *we* authored. It does not cover generated
> output, downloaded packages, or the virtual environment.

---

## A. The business case — what problem this solves

**The domain.** Live-commerce retailers sell through *live broadcast* (TV + social streams)
backed by an online shop and app. The defining mechanic: **a product shown on air by a
presenter, in a specific time window, triggers a simultaneous sales spike across every
channel** — call-centre, web, and app. Returns are heavy (think fashion and jewellery), and
everything is cross-channel.

**The questions the business actually asks — and couldn't easily answer:**

| Business question | Why it's hard | What this product delivers |
|---|---|---|
| Which **airtime segments** (product × presenter × time window) earn the most **per broadcast minute**? | No system links an on-air moment to the orders it caused | `mart_segment_performance` → **revenue per broadcast minute** |
| Does going on air actually **lift conversion**, and is it *significant*? | "Conversion of what?" — needs a real denominator (traffic), not just sales counts | `mart_conversion_windows` + a **two-proportion z-test** |
| Which products/presenters/categories drive the **highest return rates**, eroding net revenue? | Returns arrive days later, on different channels | `fact_returns` + **net revenue after returns** KPI |
| What is **net revenue after returns** by show / presenter / channel? | Each report defined "revenue" differently | One governed **metrics mart** — a single definition |

**The core problem solved:** *attribution* — tying each order back to the **specific airtime
segment** that drove it, with point-in-time accuracy, so the business can finally measure the
ROI of live selling and make decisions (which products to feature, which presenters convert,
which lines to stop airing because returns kill the margin).

## B. The problems we solved (engineering, not just analytics)

This isn't only "a dashboard." Each layer solves a concrete, defensible problem:

1. **No dataset exists for this** → we built a **synthetic generator** with a *known*
   airtime→sales correlation, so the analytics can be **verified against ground truth** (we
   know the right answer and can prove the pipeline finds it).
2. **Attribution across overlapping airings + late orders** → an **intermediate model** with a
   documented lag window and last-touch tie-break; unmatched orders tagged as organic (`-1`).
3. **"What was the product *when it aired*?"** → **SCD Type 2** on the product dimension, so a
   sale joins to the product version valid at the moment of the broadcast.
4. **Reports disagreeing on "revenue"** → one governed **metrics mart** + a documented KPI
   catalog; BI reads marts only, never re-deriving logic in the dashboard.
5. **Dirty, multi-source, schema-changing feeds** → a **VARIANT schema-on-read** landing path
   + defensive parsing + a **quarantine** for bad rows, with a `--dirty` generator mode that
   proves the pipeline survives the worst case.
6. **Trust at scale** → **test-first** data quality (generic + singular tests), **source
   freshness**, and **CI/CD** that runs the whole build + tests on every PR.
7. **"Is it real-time enough?"** → micro-batch is the correct analytical-warehouse pattern; a
   Snowflake-native streaming upgrade path (Streams+Tasks / Dynamic Tables) is built on the
   side as a production-readiness showcase (not Kafka — see `DECISIONS.md`).

## C. Why we built what we built (the design spine)

- **dbt + Snowflake + Power BI** — a modern, governed analytics stack: dbt for tested,
  version-controlled transformations; Snowflake for separated compute + RBAC; Power BI for
  self-service consumption with row-level security.
- **Layered architecture** (RAW → STAGING → INTERMEDIATE → MARTS) — separation of concerns;
  each layer has one job, and BI reads only the conformed marts.
- **Star schema** — conformed dimensions + fact tables at stated grain; the standard, portable
  dimensional model that any BI tool consumes cleanly.
- **Engineered like software** — Git branch strategy, PRs, CI gates, documented decisions, and
  a defensibility audit. The goal is *trustworthy numbers*, not just charts.

Every non-obvious choice — and the alternative it beats — is recorded in
[`DECISIONS.md`](DECISIONS.md), rated for how strongly it holds up to scrutiny.

---

## 0. What we are building (one paragraph)

A **live-commerce airtime intelligence platform**: a data product that ties every customer
order to the **broadcast airtime segment** (a product shown on air by a presenter in a time
window) that drove it, and measures the performance of live selling — **revenue per broadcast
minute**, **conversion lift during airtime**, and **return-rate intelligence** — by presenter,
product, and channel. It is built as a governed, tested **dbt + Snowflake + Power BI** pipeline,
engineered like software (version control, tests, CI, documented decisions).

## 1. The end-to-end data flow (how it all connects)

```
 ┌────────────────────┐
 │  generator/         │  Python invents realistic data with a known
 │  generate_batch.py  │  airtime→sales correlation. Emits CSV (+ JSON).
 └─────────┬───────────┘
           │  files
           ▼
 ┌────────────────────┐
 │  load_to_snowflake  │  PUT files to an internal stage, then COPY INTO RAW.
 │  .py                │  Typed tables for CSV; a VARIANT table for the JSON feed.
 └─────────┬───────────┘
           │
           ▼
   ┌───────────────┐   dbt   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
   │   RAW schema  │ ──────▶ │   STAGING     │ ▶ │ INTERMEDIATE  │ ▶ │     MARTS     │
   │ (landing)     │         │ stg_* (clean) │   │ attribution   │   │ facts + dims  │
   └───────────────┘         └───────────────┘   └───────────────┘   │ + metrics     │
                                     │                                 └───────┬───────┘
                              SNAPSHOTS schema                                  │
                              (SCD2 product history)                           ▼
                                                                        ┌───────────────┐
                                                                        │   Power BI    │
                                                                        │ star model,   │
                                                                        │ measures, RLS │
                                                                        └───────────────┘
```

**The one rule that governs everything:** BI reads from **MARTS only**. Nothing downstream
ever queries STAGING or RAW. That single rule is what guarantees every report agrees on what
"revenue" means.

**Layer responsibilities (why there are layers at all):**
- **RAW** — land data exactly as it arrives. Never transform here. If a load fails, the problem
  is isolated to landing.
- **STAGING** — clean 1:1 with the source: rename, type, trim, deduplicate. One staging model
  per source table. No business logic, no joins across entities.
- **INTERMEDIATE** — business logic that spans entities. Here it is the airtime attribution.
- **MARTS** — the conformed star schema (facts + dimensions) plus the governed metrics. This is
  the only layer BI and analysts touch.

This separation is *not* duplication — each layer has a distinct job. A column travels from RAW
(untrusted) → STAGING (clean) → MARTS (conformed + keyed) and means something more trustworthy
at each step.

---

## 2. Repository root files

### `README.md`
The public front door. Explains what the product is, the architecture diagram, the data model,
how to run it, the engineering practices, and a build-status checklist. Written so a newcomer
understands the project without any other context.

### `load_env.ps1`
A small PowerShell helper that loads `.env` into the current session (dot-source it:
`. .\load_env.ps1`) so dbt's `env_var()` calls resolve when working on Windows.

### `requirements.txt`
The Python dependency list, pinned to minimum versions:
- `numpy`, `pandas` — data generation.
- `snowflake-connector-python` — the loader's PUT + COPY INTO.
- `dbt-snowflake` (≥1.8) — the transform engine (pulls in dbt-core). Floor is 1.8 because the
  YAML uses the modern nested `arguments:`/`config:` test syntax.
- `sqlfluff` + `sqlfluff-templater-dbt` — SQL linting (also the CI gate).
- `scipy` — the statistics (the z-test can also run with pure-Python math).
- `python-dotenv` — load Snowflake creds from a local `.env`.

### `.gitignore`
Keeps secrets and generated artifacts out of the repo: the Python venv, generated CSV/JSON
output, dbt `target/`/`dbt_packages/`/`logs/`, `profiles.yml` (credentials), `.env`, RSA keys,
`*.pbix`, and local-only notes. The dbt **seeds** are *not* ignored — they are governed source
data and must be versioned.

### `.gitattributes`
Normalises line endings to LF in the repo so Windows (dev) and Linux (CI) agree, preventing
spurious whitespace diffs. Marks binaries (`*.pbix`, images) as non-text.

### `.sqlfluff`
SQL linter config: Snowflake dialect, templated through dbt so `ref()`/`source()`/macros
resolve. Sets house style (lowercase keywords, 4-space indent, 120-char lines). This is the
style the CI lint enforces on every PR.

### `.pre-commit-config.yaml`
Local quality gate that mirrors CI: trailing-whitespace/EOF fixers, YAML check,
large-file/merge-conflict guards, and **sqlfluff-lint** (with the dbt templater). Run once with
`pre-commit install`; thereafter every commit is linted before it lands.

### `.env.example`
A template for the Snowflake credentials the loader and z-test need (`SNOWFLAKE_ACCOUNT`,
`USER`, `PASSWORD` or key-pair path, role/warehouse/database/schema). You copy it to `.env`
(which is gitignored) and fill it in. Documents how to authenticate without committing secrets.

### `docs/KPI_CATALOG.md`
The human-readable KPI governance doc: every KPI's **formula • grain • filters**, plus the
"one definition, everywhere" mechanism. This is the business-facing companion to
`mart_kpi_metrics`. If anyone asks "how is Net Revenue defined?", the answer is here, once.

### `DECISIONS.md`
The architecture-decisions audit: 16 decisions, each with the rejected alternative, a one-line
defense, and a defensibility rating (Strong / Medium / Conditional). It is deliberately honest
about the medium-strength choices (last-touch attribution, aggregate-level lift, synthetic data)
rather than pretending everything is bulletproof. This is the "defend every decision" document.

### `docs/CI_SETUP.md`
The one-time CI/CD runbook: RSA key-pair generation, registering the public key on the
Snowflake user, GitHub Secrets, and branch protection.

### `PROJECT_GUIDE.md`
This file.

---

## 3. `generator/` — the synthetic data source

No public dataset contains a broadcast/airtime dimension, so we generate the data ourselves.
Because *we* control it, we bake in a **known airtime→sales correlation** — which means we can
*prove* the downstream attribution and statistics actually detect the signal (ground truth).

### `generator/generate_batch.py`
The heart of the data source. Produces one self-consistent batch of a live-commerce business:

- **Entities built:** products (some with a second SCD2 version — a category reclass or
  price-band change *before* the window, so there is real history to track), presenters (with an
  experience tier), shows, channels (TV / web / app / call-centre), and a broadcast schedule of
  **airtime segments**.
- **The correlation (the whole point):** during a product's airtime window, orders for *that
  product* spike across channels, scaled by the presenter's tier and prime-time. Online/app
  traffic also rises during airtime (a cross-channel "halo"), so conversion lift is a real
  *rate* change, not just more volume. A steady off-air "organic" trickle of orders provides the
  baseline.
- **Tables emitted (CSV):** `dim_product`, `dim_presenter`, `dim_show`, `dim_channel`,
  `fact_airtime_segment`, `fact_order_line`, `fact_returns`, `fact_traffic` (minute-level).
- **Also emits `fact_order_line.json`** — the same order lines as semi-structured JSON,
  simulating an event/app feed with no schema guarantee (consumed by the VARIANT path).
- **`--dirty` mode (production realism):** on demand, corrupts a fraction of rows the way real
  feeds do — duplicates, null required fields, bad numeric types, orphan foreign keys,
  out-of-order/late timestamps, whitespace noise — and writes a manifest of exactly what it
  injected, so the pipeline can be shown *catching* it.
- **Reproducible:** a fixed RNG seed makes every run identical. The live loop varies the seed and
  window per batch so successive batches add new, non-colliding rows.
- **All timestamps are UTC.**

### `generator/load_to_snowflake.py`
Implements ingestion: `PUT` each file to the internal stage, then `COPY INTO` the RAW tables.
- Creates typed RAW tables (with a `loaded_at_utc` audit column the freshness checks use).
- `ON_ERROR` is configurable (`abort_statement` for strict typed feeds, `continue` for resilient
  ones) — so dirty rows can be skipped rather than failing the whole load.
- **`load_variant_order_lines`** lands the JSON feed into `raw.order_line_raw` as a single
  `VARIANT` column (schema-on-read). This load **never breaks on a schema change or bad type** —
  parsing and validation happen later in staging. This is the resilient-ingestion pattern.

### `generator/run_live.py`
The "simulated live" micro-batch loop: generate a small new batch for the next window → load it
(append) → optionally run an incremental dbt build → wait → repeat. This is the industry-standard
"live" pattern for an analytical warehouse. It deliberately does **not** use Kafka (see
`DECISIONS.md` D1 and `streaming/`); the correct real-time upgrade is Snowflake-native.

---

## 4. `snowflake/00_setup.sql` — the platform

One idempotent script (`CREATE ... IF NOT EXISTS`) that provisions everything, run once as
ACCOUNTADMIN:
- **Database** `LIVE_COMMERCE` and the five **clean schemas** (`RAW`, `STAGING`, `INTERMEDIATE`,
  `MARTS`, `SNAPSHOTS`) — named cleanly, not `dbt_<layer>`, via a macro override.
- **Three separated XSMALL warehouses** — `WH_LOAD`, `WH_TRANSFORM`, `WH_BI` — isolating
  ingestion vs. transform vs. BI compute. This is a performance story (no contention) *and* a
  cost-governance story (spend is attributable per workload; `AUTO_SUSPEND=60` caps idle cost).
- **Functional RBAC** — roles `ROLE_LOADER`, `ROLE_TRANSFORMER`, `ROLE_BI_READER` under
  SYSADMIN, each granted only what its job needs (BI_READER can read MARTS only). Access is
  enforced at the warehouse layer; Power BI RLS enforces it again at the report layer (defence in
  depth).
- **Internal stage + file formats** (CSV and JSON) for `COPY INTO`.
- A commented-out **Resource Monitor** to hard-cap trial credit spend.

---

## 5. `dbt/` — the transform project

### Project configuration

- **`dbt_project.yml`** — the project manifest. Sets the project name (`live_commerce`), the
  layer→schema routing and materializations (staging = view, intermediate = ephemeral, marts =
  table, with `fact_order_line` overriding to incremental), seeds → staging schema, snapshots →
  snapshots schema. Defines two **vars**: `attribution_lag_minutes` (30) and
  `incremental_lookback_minutes` (120) — the knobs the attribution and incremental logic read.
- **`packages.yml`** — declares `dbt_utils` (surrogate keys, `date_spine`, generic-test helpers).
- **`package-lock.yml`** — pins the exact resolved package version for reproducible CI.
- **`profiles.example.yml`** — the connection template (dev + ci targets) pointing at
  `WH_TRANSFORM` via `ROLE_TRANSFORMER`; the real `profiles.yml` is gitignored.

### Macros — `dbt/macros/`

- **`generate_schema_name.sql`** — overrides dbt's default schema naming so models land in clean
  schemas (`MARTS`, not `analytics_marts`). Keeps grants and Power BI connection strings identical
  across environments.
- **`surrogate_key.sql`** — a thin, named wrapper over `dbt_utils.generate_surrogate_key`, so
  surrogate-key construction is consistent and lives in one place. Reads as `surrogate_key([...])`
  in models.

### Seeds — `dbt/seeds/`

Governed reference data, version-controlled (unlike generated output):
- **`seed_channel.csv`** — the canonical channel list (TV/web/app/call-centre). Named
  `seed_channel` (not `dim_channel`) to avoid a name collision with the `dim_channel` model.
- **`category_return_baseline.csv`** — per-category baseline return rates and a heavy-returner
  flag, available for enrichment/reference.

### Sources & freshness — `dbt/models/staging/_sources.yml`

Declares the RAW tables as dbt **sources** with **freshness** thresholds measured against
`loaded_at_utc`. A stalled feed warns at 12h / errors at 24h (tighter — 6h/12h — on the hot order
feed). Includes the `order_line_raw` VARIANT source. Freshness proves the data is live and alerts
on a stalled pipeline.

---

## 6. STAGING models — `dbt/models/staging/`

Each is a **view**, 1:1 with its source: select, rename to clear names, cast types, `trim()`/
`lower()` text, and **deduplicate** (keep the latest row per natural key via `row_number()`).
No joins across entities, no business logic. This is where data-quality tests live heavily.

- **`stg_products.sql`** — product versions, all kept (the SCD2 snapshot needs the history).
- **`stg_presenters.sql`**, **`stg_shows.sql`** — SCD1 entities, latest row per id.
- **`stg_channels.sql`** — reads from the **seed** (`seed_channel`), the governed reference copy,
  not from raw — so channel classification is under version control.
- **`stg_airtime_segments.sql`** — broadcast segments; timestamps stay UTC.
- **`stg_order_lines.sql`** — order lines from the typed CSV feed. Transaction price and money
  measures are surfaced here (they belong in the fact lineage, never a dimension).
- **`stg_returns.sql`** — returns, linked to an order line.
- **`stg_traffic.sql`** — minute-level traffic (the conversion denominator).
- **`stg_order_lines_variant.sql`** — the *resilient* counterpart: parses the VARIANT JSON feed
  defensively with `try_cast` (a bad value becomes NULL, never a failed run), validates each row,
  and flags it `is_valid` with a `dq_failure_reason`. Schema-on-read in action.
- **`stg_order_lines_quarantine.sql`** — the rows that failed validation, **captured not dropped**,
  with the reason. A non-empty quarantine is an observable data-quality signal to backfill.

### `dbt/models/staging/_staging.yml`
Documents each staging model and attaches **generic tests**: `unique`, `not_null`,
`accepted_values` (e.g. category, channel_type, reason_code), `relationships` (referential
integrity to other staging models), and range checks (`accepted_range`). This is the test-first
data-quality layer.

---

## 7. SNAPSHOT — `dbt/snapshots/dim_product_snapshot.sql`

The **SCD Type 2** mechanism, applied **only** to products. Uses the `check` strategy on the
attributes that actually slowly change (`category`, `list_price_band`, `list_price`, `brand`,
`product_name`). On each run, when a tracked attribute changes, dbt writes a new version row with
`dbt_valid_from`/`dbt_valid_to`.

**Why only products:** we need *point-in-time* truth for products — a sale must be analysed
against the product's category/price-band **as it was when it aired**. Presenter/show attributes
don't change meaningfully over the window, so they stay SCD1 (overwrite). Historising everything
would be wasted cost and complexity.

---

## 8. INTERMEDIATE — `dbt/models/intermediate/int_orders_attributed_to_airtime.sql`

**The heart of the project.** For each order line, it finds the airtime segment that drove it.

Rules (each explicit and documented so the attribution is auditable):
1. **Candidate match** — a segment for *the same product* whose window
   `[start, end + lag_minutes]` contains the order's timestamp. The lag (var, default 30 min)
   absorbs the realistic delay between "seen on air" and "order placed/synced," and must equal the
   generator's lag.
2. **Overlap / last-touch tie-break** — if a product appears in overlapping/back-to-back windows,
   attribute to the one active at order time; ties go to the **most recent start** (the segment the
   customer most plausibly just saw).
3. **Unknown member** — orders with no qualifying segment get `attributed_segment_id = -1`
   (organic/baseline). This makes "conversion lift vs. baseline" computable and ensures no order is
   silently dropped.

It is materialised `ephemeral` (inlined into the fact) because it is glue between staging and the
fact, not something BI queries directly. Documented in `_intermediate.yml`.

---

## 9. MARTS — `dbt/models/marts/`

The conformed star schema plus the governed metrics. **This is the only layer BI reads.**

### Dimensions — `dbt/models/marts/dimensions/`
All carry a **surrogate key** and an **unknown member (-1)** row so fact joins never drop rows.

- **`dim_date.sql`** — one row per calendar day, generated by `dbt_utils.date_spine` (bounds
  derived from the data). No hand-maintained calendar.
- **`dim_time_minute.sql`** — one row per minute of day (1440 rows), with a **prime-time flag** and
  **daypart** (morning/afternoon/prime_time/late_night). Lets facts be sliced by time-of-day.
- **`dim_product.sql`** — the **SCD2** dimension, built from the snapshot. Surrogate key is **per
  version** (`product_id` + `valid_from`), so a fact joins to the product *as it was* at event
  time. `valid_from_ts`/`valid_to_ts`/`is_current` define each version's window.
- **`dim_presenter.sql`**, **`dim_show.sql`** — SCD1 (overwrite), conformed.
- **`dim_channel.sql`** — conformed from the seed.

Documented + tested in `_dimensions.yml` (key uniqueness, accepted values, SCD2 version-key
uniqueness).

### Facts — `dbt/models/marts/facts/`
Every fact states its **grain** in its doc and has surrogate FKs to the conformed dims.

- **`fact_order_line.sql`** — **grain: one row per product line per order.** The only high-volume
  fact, so the only **incremental** one (`merge` on `order_line_id`). On incremental runs it
  reprocesses only rows newer than `max(event) − lookback`, which **re-attributes late-arriving
  orders** without a full rebuild. Joins to the **point-in-time** product version (the SCD2 payoff)
  and falls back to unknown-member keys for orphans. Carries the money measures.
- **`fact_airtime_segment.sql`** — **grain: one row per broadcast segment.** Carries
  `segment_duration_minutes`, the denominator for revenue-per-broadcast-minute. Point-in-time
  product FK (the product version valid when it aired).
- **`fact_returns.sql`** — **grain: one row per returned order line.** Inherits the order line's
  product/channel keys and `sold_qty` for return-rate math.
- **`fact_traffic.sql`** — **grain: one row per channel per minute.** The conversion denominator;
  joined to channel and time-minute dims.

Documented + tested in `_facts.yml` (grain, FK relationships, ranges).

### Metrics marts — `dbt/models/marts/`
The "one definition, everywhere" surface that Power BI consumes (never re-deriving logic in DAX).

- **`mart_kpi_metrics.sql`** — the governed KPI table. **Grain:** date × product × presenter ×
  channel × attributed-segment. Stores **additive base measures** (GMV, discounts, returns refund,
  net revenue after returns, units sold, returned units, distinct orders, airtime-attributed
  revenue) that roll up to any dimension by SUM. Ratios (AOV, return rate, attributed share) are
  derived in BI from these — never stored pre-divided at a grain that would break roll-ups.
- **`mart_segment_performance.sql`** — the **hero mart**. **Grain:** one row per airtime segment.
  Computes **revenue per broadcast minute** (attributed revenue ÷ duration) plus attributed
  orders/units. This is the metric that makes the project unmistakably about live commerce.
- **`mart_conversion_windows.sql`** — **grain:** one row per `airtime_state` (on_air/off_air).
  Aggregate orders ÷ web_sessions per state → **conversion rate** and **conversion lift**. The same
  two counts feed the statistical test, so the dashboard and the test always agree. (Aggregate, not
  per-product, on purpose — sessions can't be cleanly attributed to one product per minute; this is
  the honest denominator. See `DECISIONS.md` D9.)

Documented + tested in `_marts.yml`.

**Why three marts and not one:** they sit at three *different grains* (daily-additive, per-segment,
on/off-air) that cannot be one table. Each answers a different question; collapsing them would
break roll-ups or force wrong aggregations. This is the answer to "isn't this duplication?" — it
is separation by grain, not duplication.

---

## 10. Singular tests — `dbt/tests/`

Custom-SQL business-invariant tests (each passes when it returns zero rows). These are the
test-first guardrails on the logic that generic tests can't express:

- **`assert_no_future_attribution.sql`** — no order may be attributed to a segment that started
  *after* the order (causality runs one way: air → buy).
- **`assert_attribution_within_lag_window.sql`** — every attributed order falls inside its
  segment's `[start, end + lag]` window (catches an off-by-one or wrong lag).
- **`assert_return_rate_within_bounds.sql`** — returned quantity never exceeds sold quantity, so
  return rate is always in `[0, 1]`.
- **`assert_no_valid_row_has_null_keys.sql`** — any VARIANT row marked `is_valid` genuinely has
  complete, well-typed keys; dirt cannot leak past the quarantine. This is the test that *proves*
  the dirty-data handling works.

---

## 11. `analysis/conversion_lift_test.py` — the statistics

Exactly **one** statistical test, done correctly: a **two-proportion z-test** asking *is the
conversion rate during airtime significantly higher than the off-air baseline?*
- H0: on-air conversion = off-air conversion; H1 (one-sided): on-air > off-air.
- Conversion = distinct orders ÷ web_sessions, per state. Reads the counts from
  `mart_conversion_windows` (when Snowflake is live) or recomputes the identical counts from the
  CSVs offline — so the slide and the test agree.
- Reports the two rates, absolute and relative lift, the z-statistic, the p-value, and a
  plain-language conclusion. **States its assumptions honestly** (sessions as independent trials;
  population-level, not per-user causal). No ML, no battery of tests — basic and correct.

---

## 12. `streaming/` — production-readiness showcase (not used by the demo)

Answers "this is batch — how would you make it real-time, and why isn't it already?" **Built but
deliberately not wired into the demo**, because micro-batch is correct for analytics.

- **`streaming/README.md`** — the runbook and the **"why not Kafka"** decision (Kafka's latency
  dies at the warehouse boundary; the right upgrade is Snowflake-native). States exactly when we'd
  flip the switch.
- **`01_streams_and_tasks.sql`** — a **STREAM** (CDC) on the raw order-line table + a scheduled
  **TASK** that consumes only the delta and `MERGE`s it, gated on `stream_has_data` so an idle feed
  costs nothing. The imperative approach.
- **`02_dynamic_tables.sql`** — the declarative alternative: a **DYNAMIC TABLE** with a
  `TARGET_LAG`; Snowflake maintains it incrementally and automatically. Declarative freshness.

---

## 13. What is done vs. what remains

**Done and PROVEN ON LIVE SNOWFLAKE:** the generator (+ dirty mode), the loader (+ VARIANT path),
the full dbt project (staging → intermediate → marts), the SCD2 snapshot, the attribution model,
all facts and dims, the metrics marts, the KPI catalog, the streaming showcase, the decisions
audit, and the `dbt docs` lineage graph. The whole pipeline runs green against Snowflake —
**24 models, 197 tests passing** (including 4 singular business-invariant tests). **CI/CD is green
end-to-end** on GitHub Actions (key-pair auth, isolated CI schemas, `--full-refresh`). The
**SCD2 history was demonstrated** (a product reclassification produced a new version row with a
continuous valid-from/valid-to timeline). The conversion-lift **z-test runs** and detects the
airtime effect. A **dbt exposure** puts the dashboard into the lineage graph. The **Power BI
report** (four pages — Overview, Airtime, Conversion, Returns) is built on the marts; see
**Section 18** for what each page shows.

**Possible next steps:**
- **Branch protection** on `main` so a red CI run blocks the merge (a GitHub setting — see
  `docs/CI_SETUP.md`).
- **Hosted lineage docs** — publish `dbt docs generate` output to GitHub Pages.
- The modelling extensions listed under "Open items" in `DECISIONS.md` (multi-touch
  attribution, per-product lift, Snowpipe Streaming).

See **Section 16** for the full delivery log of what was built and verified.

---

## 14. Complete per-file index

Every authored file in the project, with what it is, why it exists, and how it's used. (Excludes
generated output, downloaded packages, the venv, empty `.gitkeep` placeholders, and tool config.)

### Root

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `README.md` | Public project front door | First thing a reader sees; explains the product, architecture, how to run it |
| `load_env.ps1` | PowerShell env loader | Dot-source to load `.env` into the session before running dbt on Windows |
| `requirements.txt` | Python dependency pins | `pip install -r` to set up the generator, loader, dbt, lint, stats |
| `.gitignore` | Ignore rules | Keeps secrets, keys, venv, generated output, dbt artifacts, and local notes out of the repo |
| `.gitattributes` | Line-ending normalisation | LF in repo so Windows/Linux/CI agree; marks binaries |
| `.sqlfluff` | SQL lint config | Snowflake dialect + dbt templater + house style; enforced in CI |
| `.pre-commit-config.yaml` | Local pre-commit hooks | Mirror CI checks (lint, whitespace) before each commit |
| `.env.example` | Credential template | Copy to `.env`; documents the Snowflake env vars without committing secrets |
| `docs/KPI_CATALOG.md` | KPI governance doc | One definition (formula•grain•filters) per KPI; business companion to `mart_kpi_metrics` |
| `DECISIONS.md` | Architecture decisions audit | 16 decisions rated for defensibility; the "defend every choice" reference |
| `docs/CI_SETUP.md` | CI/CD setup runbook | Step-by-step: RSA key-pair generation, Snowflake public-key registration, GitHub Secrets, branch protection |
| `PROJECT_GUIDE.md` | This walkthrough | The file-by-file reference for understanding the project |

### `generator/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `generator/generate_batch.py` | Synthetic data generator | Invents one batch with a known airtime→sales correlation; `--dirty` injects realistic bad data; emits CSV + JSON |
| `generator/load_to_snowflake.py` | Ingestion script | PUT to stage + COPY INTO RAW (typed CSV); lands the JSON feed as VARIANT; configurable ON_ERROR |
| `generator/run_live.py` | "Simulated live" loop | Repeatedly generates + loads micro-batches and runs incremental dbt — the live ingestion pattern |

### `snowflake/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `snowflake/00_setup.sql` | Platform provisioning | Idempotent: DB, 5 clean schemas, 3 separated warehouses, functional RBAC, stage + file formats |
| `snowflake/demo_scd2.sql` | SCD2 live demonstration | Repeatable script: baseline a product version → reclassify it in RAW → re-snapshot → prove a new version row opens and the old closes gap-free |

### `dbt/` — project config

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `dbt/dbt_project.yml` | dbt project manifest | Layer→schema routing, materializations, the attribution/lookback vars |
| `dbt/packages.yml` | dbt package deps | Declares `dbt_utils` (surrogate keys, date_spine, test helpers) |
| `dbt/package-lock.yml` | Pinned package versions | Reproducible package resolution in CI |
| `dbt/profiles.example.yml` | Connection template | dev + ci targets via `WH_TRANSFORM`/`ROLE_TRANSFORMER`; real `profiles.yml` is gitignored |

### `dbt/macros/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `dbt/macros/generate_schema_name.sql` | Schema-naming override | Clean schema names (`MARTS`, not `analytics_marts`) so grants/connections are env-stable |
| `dbt/macros/surrogate_key.sql` | Surrogate-key wrapper | One consistent place for key construction; reads as `surrogate_key([...])` in models |
| `dbt/macros/drop_ci_schemas.sql` | CI schema teardown (run-operation) | Drops the `_CI`-suffixed schemas so a `--full-refresh` CI run starts from an empty namespace; a **safety no-op** unless `DBT_SCHEMA_SUFFIX` is set, so it can never drop real schemas |

### `dbt/seeds/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `dbt/seeds/seed_channel.csv` | Channel reference data | Governed, version-controlled channel list; source for `stg_channels` |
| `dbt/seeds/category_return_baseline.csv` | Category return baselines | Reference rates per category; available for enrichment |

### `dbt/models/staging/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `_sources.yml` | RAW source + freshness declarations | Declares RAW tables (incl. VARIANT) with freshness thresholds on `loaded_at_utc` |
| `_staging.yml` | Staging docs + generic tests | not_null/unique/accepted_values/relationships/range tests per staging model |
| `stg_products.sql` | Cleaned product versions | 1:1 clean; keeps all versions for the SCD2 snapshot |
| `stg_presenters.sql` | Cleaned presenters | 1:1 clean, SCD1 (latest per id) |
| `stg_shows.sql` | Cleaned shows | 1:1 clean, SCD1 |
| `stg_channels.sql` | Channels from the seed | Reads the governed seed, not raw, so classification is versioned |
| `stg_airtime_segments.sql` | Cleaned broadcast segments | 1:1 clean; UTC timestamps preserved |
| `stg_order_lines.sql` | Cleaned order lines (CSV feed) | 1:1 clean; money measures surfaced for the fact lineage |
| `stg_returns.sql` | Cleaned returns | 1:1 clean; linked to an order line |
| `stg_traffic.sql` | Cleaned minute traffic | 1:1 clean; the conversion denominator |
| `stg_order_lines_variant.sql` | Defensive VARIANT parse | `try_cast` schema-on-read; flags each row valid/invalid with a reason |
| `stg_order_lines_quarantine.sql` | Captured bad rows | Invalid VARIANT rows isolated (not dropped) for inspection/backfill |

### `dbt/snapshots/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `dbt/snapshots/dim_product_snapshot.sql` | SCD2 product history | `check` strategy on changing attrs; produces point-in-time product versions |

### `dbt/models/intermediate/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `_intermediate.yml` | Intermediate docs + tests | Documents/tests the attribution model |
| `int_orders_attributed_to_airtime.sql` | The attribution model | Ties each order to the airtime segment that drove it (lag + last-touch + unknown -1) |

### `dbt/models/marts/dimensions/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `_dimensions.yml` | Dimension docs + tests | Grain, key uniqueness, accepted values, SCD2 version-key uniqueness |
| `dim_date.sql` | Calendar dimension | `date_spine`-generated; one row per day |
| `dim_time_minute.sql` | Time-of-day dimension | 1440 rows; prime-time flag + daypart |
| `dim_product.sql` | SCD2 product dimension | Per-version surrogate key from the snapshot; unknown member -1 |
| `dim_presenter.sql` | Presenter dimension | SCD1, conformed, unknown member -1 |
| `dim_show.sql` | Show dimension | SCD1, conformed, unknown member -1 |
| `dim_channel.sql` | Channel dimension | Conformed from the seed, unknown member -1 |

### `dbt/models/marts/facts/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `_facts.yml` | Fact docs + tests | Grain stated per fact; FK relationships + range tests |
| `fact_order_line.sql` | Order-line fact (incremental) | Grain: line per order; merge + lookback re-attribution; point-in-time product FK |
| `fact_airtime_segment.sql` | Segment fact | Grain: one per segment; carries duration (revenue-per-minute denominator) |
| `fact_returns.sql` | Returns fact | Grain: one per returned line; inherits keys + sold_qty |
| `fact_traffic.sql` | Traffic fact | Grain: channel per minute; the conversion denominator |

### `dbt/models/marts/` (metrics)

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `_marts.yml` | Mart docs + tests | Grain + tests for the three metrics marts |
| `mart_kpi_metrics.sql` | Governed KPI surface | One definition per KPI; additive base measures BI consumes directly |
| `mart_segment_performance.sql` | Hero mart | Revenue per broadcast minute + attributed orders/units per segment |
| `mart_conversion_windows.sql` | Conversion lift surface | On-air vs off-air orders÷sessions; feeds dashboard + the z-test |
| `_exposures.yml` | Dashboard exposure | Declares the Power BI dashboard as a dbt **exposure** so it appears in the lineage graph (marts → dashboard) and dbt can warn when a change breaks a model it depends on |

### `dbt/tests/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `assert_no_future_attribution.sql` | Singular test | No order attributed to a segment that started after it |
| `assert_attribution_within_lag_window.sql` | Singular test | Every attributed order is inside its segment's lag window |
| `assert_return_rate_within_bounds.sql` | Singular test | Returned qty never exceeds sold qty (rate ∈ [0,1]) |
| `assert_no_valid_row_has_null_keys.sql` | Singular test | Proves no "valid"-flagged VARIANT row has null keys (dirt can't leak) |

### `analysis/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `analysis/conversion_lift_test.py` | The one statistical test | Two-proportion z-test on airtime conversion lift; runs offline (CSV) or from the mart |

### `.github/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `.github/workflows/ci.yml` | GitHub Actions CI/CD pipeline | Two jobs on every PR/push to `main`: **lint-and-parse** (offline `dbt parse` + DAG validation, always runs) and **build-and-test** (key-pair auth → generate batch → load RAW → `dbt build` + tests + freshness in isolated `_CI` schemas with `--full-refresh`). The merge gate. |

### `streaming/`

| File | What it is | Why it exists / how it's used |
|---|---|---|
| `streaming/README.md` | Streaming runbook | The "why not Kafka" decision + when we'd go real-time |
| `streaming/01_streams_and_tasks.sql` | CDC stream + task | Imperative near-real-time: stream the delta, merge on a schedule |
| `streaming/02_dynamic_tables.sql` | Dynamic table | Declarative near-real-time: a SELECT + TARGET_LAG Snowflake maintains |

---

## 15. How to talk through it in 60 seconds

> "Synthetic live-commerce data with a known airtime→sales correlation is generated in Python and
> landed in Snowflake — typed via COPY INTO, and one feed as VARIANT for schema-on-read resilience.
> dbt cleans it in staging, attributes every order to the airtime segment that drove it in an
> intermediate model, and builds a star schema in marts with an SCD2 product dimension for
> point-in-time truth. A governed metrics mart defines each KPI once — revenue per broadcast
> minute, conversion lift — so Power BI never re-derives logic. It's tested test-first (generic +
> singular tests), the conversion lift is backed by a two-proportion z-test, and every
> architectural decision is documented and rated for defensibility. Micro-batch is the deliberate
> ingestion choice for an analytical warehouse; a Snowflake-native streaming upgrade path is built
> on the side as a production-readiness showcase."

---

## 16. Latest updates — delivery log (what is built and verified)

A running record of what has actually shipped and been proven, beyond the original plan.

### Live on Snowflake
- Provisioned a Snowflake account (Enterprise, AWS, Frankfurt). Ran `00_setup.sql`: database
  `LIVE_COMMERCE`, five clean schemas, three separated XSMALL warehouses, functional RBAC roles.
- Loaded synthetic data via `PUT` + `COPY INTO` (typed CSV) **and** the VARIANT JSON feed.
- **`dbt build` is green against the warehouse: 24 models, 1 snapshot, 2 seeds, 197 tests, 0
  failures.** The incremental `fact_order_line`, the SCD2 snapshot, the attribution model, and all
  four singular tests pass on real data.

### SCD2 proven (not just built)
- Reclassified product 1 (Fashion/budget/14.35 → Jewellery/premium/249.99) in RAW, re-ran
  `stg_products` + `dbt snapshot`. Result: the snapshot **closed the old version** (stamped its
  `dbt_valid_to`) and **opened a new current version**, with the old `valid_to` exactly equal to
  the new `valid_from` — a gap-free, overlap-free Type 2 timeline. Demo is repeatable via
  [`snowflake/demo_scd2.sql`](snowflake/demo_scd2.sql).

### CI/CD green end-to-end
- GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every PR:
  **lint-and-parse** (always) and **build-and-test** (generates a batch, loads RAW, runs the full
  `dbt build` + tests + freshness against Snowflake).
- Authenticates with **key-pair** (RSA, registered on the Snowflake user; the private key stored
  base64-encoded as a GitHub Secret and decoded at runtime).
- Runs in **isolated `_CI` schemas** (`DBT_SCHEMA_SUFFIX`) with **`--full-refresh`**, so automated
  runs never race with or pollute local development — and are reproducible from zero.
- The bring-up surfaced and fixed five real CI issues (relative profiles-dir, PEM newline
  mangling, account-identifier format, incremental stale state, shared-schema race) — an authentic
  CI-against-a-warehouse debugging story.

### Lineage docs
- `dbt docs generate` against Snowflake produces the full catalog (real column types/row counts);
  the lineage graph now includes the **dashboard exposure** node (sources → … → marts → dashboard).
  Viewed locally over `http://` (file:// can't load the data files).

### Docs hosting
- The lineage site is viewed locally via `dbt docs serve` (or any static server over the
  `dbt/target/` folder). Publishing it to GitHub Pages is a straightforward next step.

---

## 17. Walkthrough guide

How to explain the project so the depth comes across. Three parts: **short summaries**, a
**demo walkthrough**, and a **design FAQ**.

### 17.1 Summaries

**60-second summary** (see Section 15 for the technical version). Business-first framing:

> "Live-commerce businesses sell on air — a presenter features a product in a time window and
> sales spike across web, app, and call-centre. But nobody could measure which airing actually
> drove which sale. I built a governed dbt + Snowflake + Power BI product that attributes every
> order to the broadcast segment that caused it, then measures revenue per broadcast minute,
> conversion lift, and return rates by presenter, product, and channel. It's engineered like
> software — tested test-first, CI runs the whole build on every PR, every KPI defined once — so
> the numbers a business user sees are consistent and trustworthy."

**2-minute summary** — add: the synthetic data with a *known* correlation (so the analytics are
verifiable against ground truth); the layered architecture (RAW → staging → intermediate → marts,
BI reads marts only); SCD2 on product for point-in-time truth; the attribution rules (lag window,
last-touch, unknown member); one correct statistical test (two-proportion z-test) rather than a
stats zoo; and the production-readiness extras (dirty-data handling + a Snowflake-native streaming
upgrade path), with a decisions log rating each choice's defensibility.

### 17.2 Demo walkthrough (what to open, in what order, what to say)

1. **The lineage graph** (`dbt docs serve` → graph icon). *"Here's the whole product as one
   picture: sources flow through staging, an attribution layer, into a star schema, and out to the
   dashboard. BI only ever reads the marts — that's what guarantees consistent KPIs."*
2. **A staging model** (e.g. `stg_order_lines`). *"One job: clean, type, dedupe — no business
   logic here."*
3. **The attribution model** (`int_orders_attributed_to_airtime`). *"This is the heart: each order
   is matched to the segment for that product within a lag window; overlaps break last-touch;
   no-match orders are tagged organic."*
4. **The SCD2 snapshot + the demo** (`demo_scd2.sql` result — the two version rows). *"When a
   product is reclassified, the snapshot versions it automatically, so a sale joins to the product
   as it was when it aired."*
5. **The metrics mart + KPI catalog**. *"Every KPI defined once. Revenue per broadcast minute and
   conversion lift are the hero metrics — and the lift is backed by a z-test, not eyeballing."*
6. **A `dbt build` run + the tests passing**. *"197 tests, including business invariants like
   'returns can't exceed sales' and 'no order attributed to a future broadcast'."*
7. **CI green on a PR**. *"Every PR runs the whole build and test suite against Snowflake with
   key-pair auth, in an isolated schema. A red run blocks the merge."*
8. **The Power BI dashboard** (Section 18). *"Self-service, with row-level security so a category
   manager sees only their products."*

### 17.3 Design FAQ — common questions and answers

**"What's the grain of your main fact?"**
> "One row per product line per order — `fact_order_line`. I state grain for every fact; it's the
> first thing that goes wrong if it's fuzzy."

**"How do you stop two reports disagreeing on revenue?"**
> "One governed metrics mart defines each KPI once, it's tested and documented in a KPI catalog,
> and BI reads only the marts — no KPI logic re-implemented in DAX."

**"Why SCD2 on product but not presenter?"**
> "Point-in-time analysis needs product history — a sale must be measured against the product's
> category and price band at the time it aired. Presenter attributes don't change meaningfully
> over the window, so Type 1 (overwrite) is correct. Historising everything is wasteful — I only
> pay for history where the analysis needs it."

**"Order during overlapping airings? Late order? Timezone skew?"**
> "Documented rules: attribute to the segment active at order time for that exact product; ties
> break to most-recent-start (last-touch). A lag window absorbs the seen-on-air-to-purchase delay,
> and the incremental lookback re-attributes late-arriving orders. Everything's UTC until the BI
> layer. A singular test enforces 'no order attributed to a future segment'."

**"Why incremental on only one model?"**
> "Incremental earns its complexity only at volume. `fact_order_line` is the high-volume fact;
> everything else rebuilds in seconds and is safer fully refreshed. I can defend it by grain and
> volume."

**"How do you handle dirty or schema-changing source data?"**
> "I land the semi-structured feed as VARIANT — schema-on-read — so a source change never breaks
> the load. Staging parses defensively with `try_cast`, routes bad rows to a quarantine with a
> reason, and a singular test proves no 'valid' row has null keys. My generator has a dirty mode
> that injects nulls, dupes, bad types, and orphans so I can prove the pipeline survives it."

**"Why not streaming / Kafka — this is batch?"**
> "It's an analytical-warehouse workload; minutes-fresh KPIs are correct, and Kafka's latency
> advantage dies at the warehouse boundary. Micro-batch is the standard pattern. If real-time were
> required, the right upgrade is Snowflake-native — Streams + Tasks or Dynamic Tables — which I
> built on the side, not a second distributed system."

**"What's your statistics story?"**
> "Exactly one test, done correctly: a two-proportion z-test on whether on-air conversion exceeds
> the off-air baseline. Conversion uses a real denominator — web sessions from `fact_traffic`. I
> report the statistic, the p-value, and a plain-language conclusion, and I state assumptions
> honestly. One correct test beats a pile of half-justified ones."

**"How is this 'engineered like software'?"**
> "Git with short-lived feature branches and PRs, CI that runs `dbt build` + tests + freshness +
> lint on every PR with branch protection, tests written test-first, a decisions log that rates
> each choice's defensibility, and a separation of concerns across layers. The aim is trustworthy
> numbers, not just charts."

**"If your incremental logic were wrong, how would you recover?"**
> "`dbt build --full-refresh` rebuilds from scratch; Snowflake Time Travel restores prior state;
> a zero-copy clone lets me test a fix in isolation before applying it."

**"How is access controlled end to end?"**
> "Snowflake RBAC — functional roles (loader / transformer / BI-reader) granted only what each job
> needs, enforced at the warehouse. Power BI RLS enforces again at the report layer. Defence in
> depth."

### 17.4 "Why did you…" / "How did you build…" — design & build questions

The questions that test whether each choice is *understood* rather than copied. Each answer
leads with the reason, then the alternative it beats.

**"Walk me through how you built the dbt project — what's the workflow?"**
> "Bottom-up by layer. I declared the RAW tables as **sources** (with freshness), wrote one
> **staging view per source** to clean/type/dedupe, put the cross-entity business logic — the
> airtime attribution — in an **intermediate** ephemeral model, then built the **marts**: a star
> schema (conformed dims + facts at a stated grain) and three governed metrics marts. Every model
> has a `.yml` with docs and tests; I ran `dbt build` to run-and-test in dependency order, and
> `dbt docs generate` for the lineage graph. The layer→schema routing and materializations live in
> `dbt_project.yml`; reusable logic lives in macros and `dbt_utils`."

**"How does dbt actually know the build order?"**
> "It doesn't from filenames — it builds a DAG from the `ref()` and `source()` functions. Every
> model references its parents by `ref('…')`, so dbt infers the dependency graph and runs them in
> topological order, parallelising independent branches. That's also what powers the lineage graph
> and selectors like `dbt build -s +mart_segment_performance` (build that model and everything
> upstream of it)."

**"Why views for staging, tables for marts, ephemeral for intermediate, incremental for one fact?"**
> "Materialization is a cost/freshness trade-off chosen per layer. **Staging = view**: always
> fresh, no storage, it's just a cleaning lens over RAW. **Intermediate = ephemeral**: it's glue,
> not something BI queries, so dbt inlines it into the fact as a CTE — no extra object to maintain.
> **Marts = table**: BI hits them repeatedly, so I pay storage once for fast reads. **`fact_order_line`
> = incremental**: it's the only high-volume fact, so a full rebuild every run is wasteful;
> incremental `merge` reprocesses only recent rows. Incremental earns its complexity only at volume —
> I can defend it by grain."

**"Why dbt + Snowflake + Power BI specifically — why not pandas, or Airflow, or a notebook?"**
> "Because the goal is *trustworthy, governed* numbers, not a one-off analysis. dbt gives me
> version-controlled, tested, documented SQL transformations with lineage — a notebook gives none of
> that. Snowflake separates storage from compute and gives RBAC, Time Travel, and zero-copy clones.
> Power BI gives self-service consumption with RLS. Airflow orchestrates *tasks*; dbt models *data
> transformations* — they're complementary, and for this scope dbt's own DAG is enough. pandas would
> melt at warehouse volume and can't be tested or governed the same way."

**"Why a star schema and not just one big wide table (OBT)?"**
> "Conformed dimensions are reusable across every fact and BI tool, keep the model intuitive for
> business users, and let me apply SCD2 to *one* dimension without rewriting facts. A single wide
> table duplicates dimension attributes across millions of rows, makes point-in-time history nearly
> impossible, and re-explodes every time a dimension attribute changes. The star is the portable,
> governed choice; OBT is a query-performance optimisation I'd only reach for if measured."

**"How did you decide the grain of each table — and why does grain matter so much?"**
> "Grain is the single most important design decision: it's the level one row represents, and it
> dictates what's additive. I fixed it first for every fact (`fact_order_line` = one product line
> per order; `fact_airtime_segment` = one segment; `fact_traffic` = one channel per minute) and
> documented it. The three marts sit at three *different* grains on purpose — daily-additive,
> per-segment, on/off-air — which is why they can't be one table. Get grain wrong and every
> aggregate silently double-counts."

**"Why store additive base measures in the KPI mart instead of pre-computed ratios?"**
> "Ratios don't roll up. If I stored `return_rate` per row and a user filtered to a category, summing
> rates is meaningless. So I store the additive *numerators and denominators* (units sold, units
> returned, GMV, net revenue) and let BI compute the ratio in filter context —
> `DIVIDE(SUM(returned), SUM(sold))`. The KPI is defined once in the mart's numerator/denominator and
> stays correct at every aggregation level."

**"How do your tests work, and what would actually fail?"**
> "Two kinds. **Generic tests** in YAML — `not_null`, `unique`, `accepted_values`, `relationships`
> (referential integrity), `accepted_range` — declared next to each column. **Singular tests** —
> custom SQL that passes when it returns zero rows, for business invariants generic tests can't
> express: 'no order attributed to a future segment', 'every attributed order inside its lag window',
> 'returns never exceed sales', 'no valid-flagged VARIANT row has null keys'. `dbt build` runs every
> model then its tests in dependency order; a failing test fails the build, and in CI that blocks the
> merge. 197 tests pass on live data."

**"How does the SCD2 snapshot actually work under the hood?"**
> "`dbt snapshot` uses the `check` strategy: on each run it compares the current source row against
> the stored version on the tracked columns (category, price band, list price, brand, name). If any
> changed, it stamps the old version's `dbt_valid_to` and inserts a new row with `dbt_valid_to = NULL`
> — a gap-free, overlap-free timeline. The mart's surrogate key is per *version* (`product_id` +
> `valid_from`), so a sale joins to the product as it was when it aired. I proved it live with
> `demo_scd2.sql`."

**"How is the attribution logic implemented — and how do you know it's correct?"**
> "An intermediate model matches each order to a segment for the *same product* whose window
> `[start, end + lag]` contains the order timestamp; overlaps break to most-recent-start (last-touch);
> no match gets the unknown member `-1` (organic). I know it's correct two ways: the generator bakes
> in a *known* airtime→sales correlation, so I have ground truth to verify against; and singular tests
> enforce the invariants (no future attribution, within-lag-window) on every build."

**"How did you set up CI against a real warehouse — what was hard?"**
> "GitHub Actions runs `lint-and-parse` always (offline `dbt parse` validates the DAG) and
> `build-and-test` when Snowflake secrets exist. CI authenticates with a **key-pair** (no password):
> the private key is stored **base64-encoded** as a secret and decoded to a PEM at runtime — that
> base64 step is the fix for GitHub mangling the newlines in a raw multiline PEM. It builds into
> isolated `_CI` schemas with `--full-refresh`, after a `drop_ci_schemas` run-operation that tears
> them down first so seeds don't collide. The bring-up surfaced five real issues — relative
> profiles-dir, PEM newline mangling, account-identifier format, stale incremental state, and a
> shared-schema race — all documented in `docs/CI_SETUP.md`."

**"Why micro-batch and not streaming? Isn't 'live commerce' real-time?"**
> "The *selling* is live; the *analytics* don't need sub-second freshness — minutes-fresh KPIs are
> correct for decision-making, and Kafka's latency advantage dies at the warehouse load boundary
> anyway. Micro-batch is the standard analytical-warehouse pattern. If real-time were required, the
> right upgrade is **Snowflake-native** (Streams + Tasks or Dynamic Tables), which I built in
> `streaming/` as a showcase rather than bolting on a second distributed system."

**"Why three separate warehouses and functional roles — isn't that over-engineering for a demo?"**
> "It's the production pattern, and it's cheap to do right from the start. Separate `WH_LOAD` /
> `WH_TRANSFORM` / `WH_BI` means ingestion, transform, and BI never contend for compute, and spend is
> attributable per workload. Functional roles (loader / transformer / BI-reader) grant each job only
> what it needs — BI_READER reads MARTS only — enforced at the warehouse, with Power BI RLS enforcing
> again at the report layer. Defence in depth, and it shows I think about cost and access, not just
> charts."

### 17.5 Known limitations (stated honestly)

- **Synthetic data:** "No public dataset has a broadcast/airtime dimension. Synthetic with a known
  correlation is a feature — it gives ground truth to verify the analytics. And I added a dirty
  mode so robustness is demonstrated, not assumed."
- **Conversion lift is population-level, not per-product:** "Sessions can't be cleanly attributed
  to one product per minute, so the aggregate rate is the honest denominator — which is exactly
  what the z-test consumes. Per-product lift would need a session-attribution model I deliberately
  didn't over-build." (Both noted in `DECISIONS.md`.)

---

## 18. Dashboard insights — the story each chart tells

The Power BI report is **four pages**, navigated by the top-right tab buttons
(Overview · Airtime · Conversion · Returns). Every visual reads from the **marts only**, so each
number traces back to one tested KPI definition. Below: the headline insight per visual, plus the
slicer that turns the chart from a number into a finding. Use these as talking points in a walkthrough.

### Page 1 — Live Commerce Performance (Executive Overview)

*Slicer: **Select Month** — the whole page is a monthly drill of the executive numbers.*

- **KPI cards.** The business in five figures: **€260 revenue per broadcast minute** (the hero
  efficiency metric — every minute of air is worth €260 net), **€50.68M net revenue after returns**,
  **€62.90M gross GMV**, **+81.52% on-air conversion lift**, and a **12.91% blended return rate**.
  *Insight:* the gap between €62.9M GMV and €50.68M net (~€12M) is the cost of returns made visible —
  net-after-returns is the only honest revenue number, and the dashboard leads with it.
- **Net revenue over time (line + area).** Net revenue (red) sits consistently above
  airtime-attributed revenue (the shaded band). *Insight:* **~two-thirds of revenue is
  airtime-driven every month** — live selling isn't a spike, it's the baseline engine. Drive the
  **Month** slicer to show the band tracking net revenue in lockstep — airtime is structural, not seasonal.
- **Revenue by channel (donut).** Web Shop **42.6% (€21.6M)** leads, Mobile App **29.6% (€15M)**,
  Call Centre **19.1%**, TV Linear **8.7%**. *Insight:* TV is the *trigger* (it drives the airtime
  spike) but **digital channels capture the sale** — the cross-channel halo is real and the donut
  proves the money lands on web/app, not the broadcast channel itself.
- **Top product by net revenue (table).** Turbo Sound Bar and Restore Probiotic top the list at
  **€1.84M** each, with return rates (5.6–7.8%) shown alongside revenue. *Insight:* the table pairs
  revenue *with* return rate so a "top seller" with a hidden return problem can't masquerade as a
  winner — net revenue and return rate are always read together.

### Page 2 — Airtime Performance (Presenter & Segment ROI)

*Slicer: **Experience Tier** (Star / Established / Rookie) — re-ranks every visual by presenter calibre.*

- **KPI cards.** **€260 revenue/broadcast-minute**, **€34M airtime-attributed revenue**,
  **66.84% airtime revenue share**, **130.38K total broadcast minutes**. *Insight:* two-thirds of
  all revenue is attributable to a specific airing — this single share number is the project's whole
  thesis ("does airtime convert?") answered in one figure.
- **Revenue by presenter (bar, coloured by tier).** Stars **Sofia P5 (€4.4M), Lena P3 (€4.0M),
  David P10 (€3.8M)** lead; Established presenters cluster at €3.0–3.5M. *Insight:* presenter tier is
  a genuine revenue lever, not a label — Stars out-earn Established by ~25–45%. Set the **Experience
  Tier** slicer to *Rookie* to show the floor and quantify the ROI of developing talent.
- **Revenue per broadcast minute by daypart (bar).** **Prime Time crushes it at €316/min** vs.
  ~€200/min for Late Night, Morning, Afternoon. *Insight:* prime-time air is **~55% more productive
  per minute** — so the scheduling question isn't "air more," it's "air the right products in prime
  time." This is the actionable efficiency finding.
- **Category × Presenter matrix (heatmap).** Sofia P5 peaks in Wellness (€1.16M) and Fashion
  (€0.92M); presenters have distinct category strengths. *Insight:* the heatmap is a **casting tool** —
  it shows which presenter to put on which category to maximise revenue per slot, not just who's
  good overall.

### Page 3 — Conversion Performance (Does Airtime Convert?)

*Slicer: **Airtime State** (On-Air / Off-Air) legend split runs through every chart — this is the page's core comparison.*

- **KPI cards + significance banner.** **+81.52% average conversion lift**, **On-Air 0.05% vs
  Off-Air 0.03%**, across **559.9K distinct orders** — and a green **"Statistically Significant
  (p < 0.01, two-proportion z-test)"** callout. *Insight:* the lift isn't eyeballed. The same two
  counts that feed the chart feed the z-test, so the dashboard and the statistics can never disagree —
  airtime *genuinely* drives conversion, not noise.
- **Conversion rate by airtime state (bar).** On-Air **0.046%** vs Off-Air **0.025%** — the
  headline 2× rate change made literal. *Insight:* this is a real *rate* change (orders ÷ sessions),
  not just more volume — the honest denominator (web sessions from `fact_traffic`) is what makes the
  lift defensible.
- **Conversion by daypart × airtime state (grouped bar).** On-Air beats Off-Air in **every
  daypart**; the gap is widest in **Morning (0.053% vs 0.024%)** and **Afternoon (0.051% vs 0.031%)**.
  *Insight:* the airtime effect is universal across the day, but the *biggest relative lift* is in
  off-peak dayparts — air during quiet hours and you move the rate the most.
- **Distinct orders & web sessions by state (bars).** On-Air drives **0.36M orders** vs 0.20M
  off-air, on **comparable session volume** (0.77bn on-air vs 0.80bn off-air). *Insight:* this is the
  proof the lift is a *rate* effect, not a traffic effect — similar sessions, far more orders when on air.

### Page 4 — Return Intelligence (Where Returns Concentrate & What They Cost)

*Slicer: **Price Band** (All / Budget / Mid / Premium) — re-cuts the return story by price tier.*

- **KPI cards.** **12.91% return rate**, **€7.5M refunded**, **€50.68M net revenue after returns**,
  **93.94K units returned**. *Insight:* returns are a **€7.5M leak** — surfacing it as a first-class
  KPI (not buried in net) is what lets the business act on it.
- **Return rate per category (bar).** **Fashion 26.86%** and **Jewellery 22.23%** dwarf Electronics
  (5.62%) and Wellness (6.69%). *Insight:* returns are **category-concentrated** — fashion/jewellery
  fit-and-style returns are 4–5× electronics. The margin conversation belongs to *those two categories*.
- **Returns by reason code (donut).** **Size/Fit 40%** and **Changed Mind 25%** dominate; Damaged and
  Late Delivery are minor. *Insight:* the top reason is **addressable** — better sizing guidance on
  air and on the PDP attacks 40% of returns directly. It's a merchandising fix, not a logistics one.
- **Return rate: Category × Price Band (matrix).** Fashion runs **24–30%** across every band, peaking
  at **Premium (29.66%)** and Luxury (29.45%). *Insight:* drive the **Price Band** slicer to *Premium* —
  return rates rise with price in the returny categories, so **premium fashion/jewellery returns erode
  the most absolute margin**. That's where a returns-reduction program pays back fastest.

### How to demo the slicers (one line each)

| Page | Slicer | What to show |
|---|---|---|
| Overview | Select Month | The airtime-attributed band tracks net revenue every month → airtime is structural, not seasonal |
| Airtime | Experience Tier | Star → Rookie collapses the revenue bars → quantifies the ROI of presenter calibre |
| Conversion | Airtime State | On-Air vs Off-Air everywhere → the lift holds in every daypart and channel |
| Returns | Price Band | All → Premium → return rates climb with price in Fashion/Jewellery → where margin leaks most |

**The one-sentence dashboard story:** *Airtime drives ~two-thirds of revenue and a statistically
significant conversion lift (best per-minute in prime time, best per-presenter from Stars), while
returns concentrate in premium Fashion & Jewellery on Size/Fit — so the growth play is "air the
right products with the right presenters in prime time," and the margin play is "fix sizing in the
two returny categories."*
