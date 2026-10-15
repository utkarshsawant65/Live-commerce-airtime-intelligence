# Project Guide

How the Live Commerce Airtime Intelligence Platform is put together, layer by layer. Design
rationale and rejected alternatives are in [DECISIONS.md](DECISIONS.md); KPI formulas are in
[docs/KPI_CATALOG.md](docs/KPI_CATALOG.md).

## 1. Business case

Live-commerce retailers sell through live broadcast backed by a web shop, app and call
centre. A presenter features a product for a few minutes and orders for it rise on every
channel. Returns are heavy in some categories (fashion, jewellery), and they arrive days
later, often through a different channel.

| Question | Why it is hard | Where it is answered |
|---|---|---|
| Which segments earn the most per broadcast minute? | Orders are not linked to the airing that caused them | `mart_segment_performance` |
| Does being on air lift conversion, and is it significant? | Needs a traffic denominator, not just order counts | `mart_conversion_windows` + z-test |
| Which products, presenters and categories drive returns? | Returns arrive later and on other channels | `fact_returns`, `mart_kpi_metrics` |
| What is net revenue after returns by show, presenter, channel? | Each report defines revenue differently | `mart_kpi_metrics` |

The core problem is attribution: tying each order to the airtime segment that drove it, so
the business can see which products, presenters and time slots pay off.

## 2. Architecture and data flow

```
generator/generate_batch.py      CSVs + fact_order_line.json + _batch_manifest.json
        |
generator/load_to_snowflake.py   PUT to raw.stg_landing, COPY INTO RAW
        |                        (typed tables for CSV, VARIANT table for JSON)
        v
RAW --> STAGING --> INTERMEDIATE --> MARTS --> Power BI
        stg_*       attribution      facts, dims, metric marts
           \
            SNAPSHOTS (dim_product_snapshot, SCD2)
```

| Layer | Schema | Materialisation | Job |
|---|---|---|---|
| Raw | `RAW` | tables (loader) | data as it arrived, plus `loaded_at_utc` |
| Staging | `STAGING` | views | rename, cast, trim, dedupe; one model per source |
| Intermediate | none | ephemeral | attribution logic, inlined into `fact_order_line` |
| Marts | `MARTS` | tables (`fact_order_line` incremental) | star schema and metric marts |
| Snapshots | `SNAPSHOTS` | snapshot | product history |

Power BI reads `MARTS` only. `generate_schema_name` writes to these exact schema names (no
`<target>_` prefix), appending `DBT_SCHEMA_SUFFIX` when set (CI uses `_CI`).

Snowflake setup (`snowflake/00_setup.sql`): database `LIVE_COMMERCE`, the five schemas, three
XSMALL warehouses (`WH_LOAD`, `WH_TRANSFORM`, `WH_BI`, auto-suspend 60 s), roles
`ROLE_LOADER`, `ROLE_TRANSFORMER`, `ROLE_BI_READER` under SYSADMIN, the internal stage and
CSV/JSON file formats. Grants run as SECURITYADMIN; an optional resource monitor is included
commented out.

## 3. Data generation and loading

**`generate_batch.py`** builds one consistent batch: 120 products (about 25% with an earlier
version in the source table), 10 presenters with an experience tier, shows, four channels
(TV Linear, Web Shop, Mobile App, Call Centre), a broadcast schedule of airtime segments,
order lines, returns and minute-level traffic. During a segment and the 30 minutes after it,
orders for the featured product rise across channels, scaled by presenter tier and prime
time, and online traffic rises too. Off-air there is a baseline of organic orders. All
timestamps are UTC and a fixed `--seed` makes output reproducible.

Flags: `--out --start --days --seed --products --presenters --order-seq-start
--segment-seq-start --dirty --dirty-rate`.

**Dirty mode.** `--dirty` corrupts only the JSON order-line feed; the typed CSVs stay clean so
a strict `COPY INTO` never aborts. Each corruption type hits its own rows (`--dirty-rate`,
default 2% per type), and `_batch_manifest.json` records the exact counts:

| Injected | Where it shows up |
|---|---|
| duplicate order lines | first copy stays valid; the rest go to quarantine, `duplicate_order_line` |
| null `product_id` | quarantine, `missing_or_bad_product_id` |
| non-numeric `quantity` ("N/A", "two", ...) | `try_cast` to null, quarantine, `missing_or_bad_quantity` |
| orphan `product_id` | quarantine, `unknown_product_id` |
| event time 2-10 days earlier | passes validation as late-arriving rows |

**`load_to_snowflake.py`** creates the RAW tables if needed, `PUT`s each file to
`raw.stg_landing` and runs `COPY INTO`. CSV loads use `--on-error` (default
`abort_statement`); the JSON feed lands in `raw.order_line_raw` as one `VARIANT` per row with
`on_error = 'continue'`. `--truncate` reloads instead of appending. Password or key-pair auth
comes from `.env` (see `.env.example`).

**`run_live.py`** loops: generate the next window, append it to RAW, optionally `dbt build`
(`--run-dbt`), wait `--interval` seconds. Other flags: `--iterations --batch-days --start
--seed --products --presenters --no-load`. Dimensions are generated from `--seed` and
`--start` so they stay the same across batches; the fact seed follows the window. Window
start and order/segment ids continue from the previous batch's manifest.

## 4. Staging

One view per source, 1:1: cast types, `trim`/`lower` text, and dedupe with `row_number()`
keeping the latest load per natural key. No joins across entities.

- `stg_products` keeps every source version (`product_id`, `product_version`).
- `stg_presenters`, `stg_shows`: latest row per id.
- `stg_channels` reads the `seed_channel` seed rather than RAW, so channel classification is
  version-controlled.
- `stg_airtime_segments`, `stg_order_lines`, `stg_returns`, `stg_traffic`: typed CSV feeds.
- `stg_order_lines_variant` parses `raw.order_line_raw` with `try_cast` (a bad value becomes
  null, the run does not fail) and sets `is_valid` and `dq_failure_reason`.
- `stg_order_lines_quarantine` holds the invalid rows with their reason.

Sources are declared in `_sources.yml` with freshness on `loaded_at_utc`: warn at 12 h, error
at 24 h; 6 h / 12 h for the two order-line feeds.

The facts are built from the typed CSV feed. The VARIANT path is a parallel landing route
that shows schema-on-read and quarantine handling.

## 5. Product history (SCD2)

`dbt/snapshots/dim_product_snapshot.sql` snapshots the current product row from
`stg_products` with the `check` strategy on `category`, `list_price_band`, `list_price`,
`brand` and `product_name`, and `hard_deletes='invalidate'`.

- When a tracked attribute changes, the next snapshot run closes the old version
  (`dbt_valid_to`) and opens a new one. Versions are effective from the run that captured the
  change, so they follow processing time, not a source effective date.
- `dim_product` builds one row per version with surrogate key (`product_id`, `dbt_valid_from`).
  The first version of each product is widened back to 1900-01-01 so events before the first
  snapshot still resolve; the current version runs to 9999-12-31.
- Facts join to the version whose `[valid_from_ts, valid_to_ts)` contains the event time.

`snowflake/demo_scd2.sql` walks through it: query product 1's versions, change its category
and price in RAW, run `dbt run --select stg_products` and `dbt snapshot`, and query again to
see the old version closed and a new current one opened.

Only product is SCD2; presenter and show are SCD1 (D4 in DECISIONS.md).

## 6. Attribution

`int_orders_attributed_to_airtime` (ephemeral) tags each order line with a segment:

1. **Candidates:** segments for the same product whose window
   `[start_ts_utc, end_ts_utc + attribution_lag_minutes)` contains `event_ts_utc`. The lag is a
   dbt var (30) and must match `LAG_MINUTES` in the generator and the z-test.
2. **Tie-break:** latest start wins (last touch), then highest segment id.
3. **No match:** `attributed_segment_id = -1`, `is_airtime_attributed = false` (organic).

Two singular tests guard it: no order is attributed to a segment that started after it, and
every attributed order falls inside its segment's window.

## 7. Facts and dimensions

| Model | Grain | Notes |
|---|---|---|
| `fact_order_line` | one row per order line | incremental merge; money measures; point-in-time `product_key` |
| `fact_airtime_segment` | one row per segment | duration, daypart and prime-time flag from the start hour; product version valid at start |
| `fact_returns` | one row per returned line | carries the sale's product, channel and segment, plus `sold_qty` |
| `fact_traffic` | one row per channel per minute | viewers and web sessions |
| `dim_date` | one row per day | `dbt_utils.date_spine`, clipped to the dates in the data |
| `dim_time_minute` | one row per minute of day (1,440) | daypart: morning 05-11, afternoon 12-17, prime time 18-22, late night otherwise |
| `dim_product` | one row per product version | SCD2 (section 5) |
| `dim_presenter`, `dim_show` | one row per id | SCD1 |
| `dim_channel` | one row per channel | from the seed |

Product, presenter, show and channel have an unknown member (`-1`, `Unknown`) that orphan
facts join to.

**Incremental `fact_order_line`.** `merge` on `order_line_id`. Each incremental run reprocesses:

- rows within `incremental_lookback_minutes` (120) of the latest `event_ts_utc` already in the
  table, so recent orders are re-attributed, and
- any row with `loaded_at_utc` later than the latest one in the table, whatever its event
  time, so late-arriving rows are picked up.

`dbt build --full-refresh` rebuilds it from scratch.

## 8. Marts

Three marts at three grains:

| Mart | Grain | Purpose |
|---|---|---|
| `mart_kpi_metrics` | date × product × presenter × channel × attributed segment | additive base measures: GMV, discounts, returns refund, net revenue after returns, total net revenue, units sold, returned units, distinct orders, airtime-attributed revenue |
| `mart_segment_performance` | one row per segment | attributed orders, units, revenue, and revenue per broadcast minute |
| `mart_conversion_windows` | airtime state × date × channel × daypart | online orders and web sessions on air vs off air |

Ratios (AOV, return rate, attributed share) are computed in Power BI from the summed base
measures, so they stay correct at any level of aggregation.

**Conversion definition.** Online channels only (Web Shop, Mobile App), for both orders and
sessions. A minute is `on_air` if it falls inside any segment's window
`[start, end + 30 min)`, otherwise `off_air`; orders take the state of their order minute and
are counted as distinct `order_id`. This rule is about broadcast time, not the product, and
is separate from `is_airtime_attributed` on `fact_order_line`, which is what attributed revenue
uses. Sum `orders` and `web_sessions` before dividing.

`_exposures.yml` declares the Power BI report ("Live Commerce Airtime Dashboard") as a dbt
exposure on the marts, facts and dimensions, so it appears in the `dbt docs` lineage graph.

## 9. Tests

The project has 185 data tests across 24 models, 1 snapshot and 2 seeds (`dbt ls`).

- **Generic tests** in the YAML files: `unique`, `not_null`, `accepted_values`,
  `relationships` and `dbt_utils` range checks, plus SCD2 version-key uniqueness.
- **Singular tests** in `dbt/tests/` (pass when they return no rows):

| Test | Checks |
|---|---|
| `assert_no_future_attribution` | no order attributed to a segment that started after it |
| `assert_attribution_within_lag_window` | every attributed order is inside `[start, end + lag)` |
| `assert_return_rate_within_bounds` | returned quantity never exceeds sold quantity per line |
| `assert_no_valid_row_has_null_keys` | no VARIANT row marked valid has missing or out-of-range fields |

`dbt build` runs models and their tests in DAG order; a failing test fails the build.

## 10. Statistics

`analysis/conversion_lift_test.py` runs a one-sided two-proportion z-test:
H0 on-air conversion equals off-air, H1 on-air is higher. It uses only the standard library.

- `--in generator/output` recomputes the counts from the CSVs with the same rules as the mart.
- `--from-snowflake` sums `marts.mart_conversion_windows` by state, connecting as
  `SNOWFLAKE_BI_ROLE` / `SNOWFLAKE_BI_WAREHOUSE` (default `ROLE_BI_READER` / `WH_BI`).
- `--alpha` sets the significance level (default 0.05).

It prints both rates, absolute and relative lift, z and p. Sessions are treated as
independent trials, so the result is a population-level lift, not a per-user causal effect.

Reproducible result:

```bash
python generator/generate_batch.py --out generator/output --days 5 --seed 42
python analysis/conversion_lift_test.py --in generator/output
```

| | Orders | Sessions | Conversion |
|---|---|---|---|
| On air | 4,653 | 18,222,830 | 0.026% |
| Off air | 1,309 | 9,688,824 | 0.014% |

Relative lift +89.0%, z = 20.69, p ≈ 2e-95.

## 11. CI/CD

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`, one run at a time:

- `parse` (always): `dbt parse` offline, then a 2-day generator run and the z-test.
- `check-secrets`: detects whether `SNOWFLAKE_ACCOUNT` is configured.
- `build-and-test` (only with secrets): key-pair auth, load a 2-day batch into RAW with
  `--truncate`, `drop_ci_schemas`, `dbt build --target ci --full-refresh` into `_CI` schemas
  (`STAGING_CI`, `MARTS_CI`, `SNAPSHOTS_CI`, ...), `sqlfluff lint` (blocking), and
  `dbt source freshness` (report only).

The profile target comes from `DBT_TARGET` (default `dev`, password auth; `ci` uses a private
key). The CI user needs `ROLE_LOADER` and `ROLE_TRANSFORMER`. CI reloads the shared RAW
schema, so it should point at its own database or account. Setup steps:
[docs/CI_SETUP.md](docs/CI_SETUP.md).

Locally, `.pre-commit-config.yaml` runs whitespace/YAML checks and `sqlfluff lint` with the
dbt templater (`.sqlfluff`: Snowflake dialect, lowercase, 4-space indent, 120 characters).

## 12. Near-real-time option

The default is micro-batch (D1). `streaming/` has two optional Snowflake-native scripts over
`raw.fact_order_line`: a stream plus a task that merges new rows every minute when the stream
has data, and a Dynamic Table with a one-minute target lag. Both need change tracking on the
source and, for the task, extra grants listed in the script headers. The task is created
suspended and the Dynamic Table is suspended right after creation. See
[streaming/README.md](streaming/README.md).

## 13. Dashboard

Four Power BI pages in Import mode, reading the marts only. The `.pbix` is not in the repo.
Figures below come from the larger generated dataset behind the report, except the
conversion figures, which are from the reproducible sample run in section 10.

**Overview** (slicer: month)
- Cards: €260 revenue per broadcast minute, €50.68M net revenue after returns, €62.90M GMV,
  12.91% return rate. The €12M gap between GMV and net is discounts and refunds.
- Net revenue over time with airtime-attributed revenue as a band: about two-thirds of
  revenue is airtime-attributed every month.
- Revenue by channel: Web Shop 42.6%, Mobile App 29.6%, Call Centre 19.1%, TV Linear 8.7%.
  TV triggers the demand; most orders land online.
- Top products by net revenue with their return rates alongside.

**Airtime** (slicer: presenter experience tier)
- €34M airtime-attributed revenue, 66.84% airtime share, 130.38K broadcast minutes.
- Revenue by presenter, coloured by tier: the top Star presenters (€3.8-4.4M) lead the
  Established ones (€3.0-3.5M).
- Revenue per broadcast minute by daypart: prime time €316/min vs about €200/min in other
  dayparts.
- Category × presenter heatmap: presenters have distinct category strengths, which helps
  decide who presents what.

**Conversion** (on-air vs off-air split throughout)
- On-air vs off-air conversion with the z-test result. On the sample run: 0.026% vs 0.014%,
  +89.0% relative lift, p ≈ 2e-95.
- Conversion by daypart and by channel for each state.
- Orders and web sessions by state, to show the lift is a rate change and not just more
  traffic.

**Returns** (slicer: price band)
- 12.91% return rate, €7.5M refunded, 93.94K units returned.
- Return rate by category: Fashion 26.9% and Jewellery 22.2% vs Electronics 5.6% and
  Wellness 6.7%.
- Reasons: size/fit 40% and changed mind 25%; damaged and late delivery are minor.
- Category × price band: Fashion returns run 24-30% in every band, highest at premium.

Row-level security in the report restricts rows by role on top of the Snowflake grants.

## 14. Status

- Built and run end to end on Snowflake: setup, load (typed and VARIANT), dbt build with the
  snapshot, attribution, facts, dimensions and marts.
- SCD2 change capture shown with `snowflake/demo_scd2.sql`.
- 185 data tests in the suite; CI as described in section 11.
- `dbt docs generate` lineage includes the dashboard exposure; view it with `dbt docs serve`.
- Power BI report with four pages.

Limitations and next steps: end of [DECISIONS.md](DECISIONS.md).

## 15. File index

| Path | Purpose |
|---|---|
| `README.md`, `PROJECT_GUIDE.md`, `DECISIONS.md` | Overview, this guide, design decisions |
| `docs/KPI_CATALOG.md`, `docs/CI_SETUP.md` | KPI definitions; CI setup |
| `requirements.txt` | numpy, snowflake-connector-python, dbt-snowflake ≥1.10, sqlfluff + dbt templater, python-dotenv |
| `.env.example`, `load_env.ps1` | Snowflake credential template; loads `.env` into PowerShell (bash: `set -a; source .env; set +a`) |
| `.sqlfluff`, `.pre-commit-config.yaml` | Lint config; local hooks |
| `.gitignore`, `.gitattributes` | Keeps secrets, keys, generated data and `.pbix` out; LF line endings |
| `generator/generate_batch.py` | Synthetic batch generator, `--dirty` mode, manifest |
| `generator/load_to_snowflake.py` | `PUT` + `COPY INTO` RAW; VARIANT load for JSON |
| `generator/run_live.py` | Micro-batch loop, optional `dbt build` |
| `snowflake/00_setup.sql` | Database, schemas, warehouses, roles, grants, stage, file formats |
| `snowflake/demo_scd2.sql` | SCD2 change-capture walkthrough |
| `dbt/dbt_project.yml` | Schema routing, materialisations, vars `attribution_lag_minutes` (30) and `incremental_lookback_minutes` (120) |
| `dbt/packages.yml`, `dbt/package-lock.yml` | `dbt_utils` |
| `dbt/profiles.example.yml` | `dev` (password) and `ci` (key-pair) targets, chosen by `DBT_TARGET` |
| `dbt/macros/generate_schema_name.sql` | Unprefixed schema names plus `DBT_SCHEMA_SUFFIX` |
| `dbt/macros/surrogate_key.sql` | Wrapper over `dbt_utils.generate_surrogate_key` |
| `dbt/macros/drop_ci_schemas.sql` | Drops `_CI` schemas; no-op outside the `ci` target |
| `dbt/seeds/seed_channel.csv` | Channel reference data |
| `dbt/seeds/category_return_baseline.csv` | Baseline return rate per category |
| `dbt/models/staging/` | `_sources.yml`, `_staging.yml` and ten `stg_*` models |
| `dbt/snapshots/dim_product_snapshot.sql` | Product SCD2 snapshot |
| `dbt/models/intermediate/` | `int_orders_attributed_to_airtime` and its YAML |
| `dbt/models/marts/dimensions/` | Six dimensions and `_dimensions.yml` |
| `dbt/models/marts/facts/` | Four facts and `_facts.yml` |
| `dbt/models/marts/` | Three metric marts, `_marts.yml`, `_exposures.yml` |
| `dbt/tests/` | Four singular tests |
| `analysis/conversion_lift_test.py` | Two-proportion z-test, offline or from Snowflake |
| `.github/workflows/ci.yml` | CI workflow |
| `streaming/` | Streams + Tasks and Dynamic Table scripts, README |
