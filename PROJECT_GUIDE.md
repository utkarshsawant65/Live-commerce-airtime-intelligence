# Project Guide

How the Live Commerce Airtime Intelligence Platform is put together, layer by layer. Design
rationale and rejected alternatives are in [DECISIONS.md](DECISIONS.md); KPI formulas are in
[docs/KPI_CATALOG.md](docs/KPI_CATALOG.md); validation results are in
[docs/VALIDATION.md](docs/VALIDATION.md).

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
        |                        (+ _truth_order_attribution.csv, never loaded)
generator/load_to_snowflake.py   PUT to raw.stg_landing, COPY INTO RAW
        |                        (typed tables for CSV, VARIANT table for JSON)
        v
RAW --> STAGING --> INTERMEDIATE --> MARTS --> Power BI
        stg_*       attribution      facts, dims, metric marts
           \
            SNAPSHOTS (dim_product_snapshot, capture-time audit trail)
```

| Layer | Schema | Materialisation | Job |
|---|---|---|---|
| Raw | `RAW` | tables (loader) | data as it arrived, plus `loaded_at_utc` |
| Staging | `STAGING` | views | rename, cast, trim, dedupe; one model per source |
| Intermediate | `INTERMEDIATE` | view | attribution logic, read by `fact_order_line` |
| Marts | `MARTS` | tables (`fact_order_line` incremental) | star schema and metric marts |
| Snapshots | `SNAPSHOTS` | snapshot | product audit trail |

Power BI reads `MARTS` only. `generate_schema_name` writes to these exact schema names (no
`<target>_` prefix), appending `DBT_SCHEMA_SUFFIX` when set (CI uses `_CI`).

Snowflake setup (`snowflake/00_setup.sql`): database `LIVE_COMMERCE`, the five schemas, three
XSMALL warehouses (`WH_LOAD`, `WH_TRANSFORM`, `WH_BI`, auto-suspend 60 s), roles
`ROLE_LOADER`, `ROLE_TRANSFORMER`, `ROLE_BI_READER` under SYSADMIN, the internal stage and
CSV/JSON file formats. Grants run as SECURITYADMIN; an optional resource monitor is included
commented out.

## 3. Data generation and loading

**`generate_batch.py`** builds one consistent batch from named constants at the top of the
file. All timestamps are UTC and a fixed `--seed` makes output reproducible.

- **Dimensions.** 120 products in six categories. About 25% have an earlier version that
  ends 30 days before the window, and about 10% change price on a day inside the window;
  orders are priced from the version valid at order time. 10 presenters with a fixed tier mix
  of 3 rookie, 5 established and 2 star (shuffled). Four channels: TV Linear, Web Shop,
  Mobile App, Call Centre.
- **Schedule.** One TV channel with fixed, non-overlapping show slots, one presenter per show
  and products drawn from the show's categories. Each show is a run of 5-14 minute segments.

  | Show | Slot (UTC) | Days | Categories |
  |---|---|---|---|
  | Morning Glow | 08-11 | all | Beauty, Wellness, Home & Living |
  | Jewellery Hour | 13-17 | weekdays | Jewellery |
  | Weekend Style Edit | 13-17 | weekends | Fashion, Beauty |
  | Prime Time Live | 18-22 | all | any |
  | Late Night Deals | 22-24 | all | any |

- **Traffic.** Online sessions and call-centre contacts are Poisson counts per minute: Web
  about 11, App about 7, Call Centre about 1.2, times an hour-of-day multiplier (0.3 before
  07:00, 2.1 in prime time 18:00-22:59, 0.8 at 23:00, else 1). While any segment window
  `[start, end + 30 min)` is open, online sessions are multiplied by the halo (`HALO` = 2.9).
  The halo changes traffic volume only, not the conversion rate.
- **Online orders.** Driven by sessions. Baseline: each online channel-minute gets
  Poisson(sessions × `BASE_CONVERSION` = 0.015) orders for a random product. Airtime: each
  segment gets Poisson(`BASE_CONVERSION` × `ONAIR_UPLIFT` (0.63) × weight × online sessions
  during the airing) incremental orders for its product, where weight = tier multiplier
  (rookie 1.05, established 1.25, star 1.6) × 1.6 in prime time × price factor
  `clip(2 − price / 400, 0.4, 1.8)`.
- **Response lag.** Each airtime order lands at segment start + lag, lag lognormal (median
  9 min, sigma 1.25, capped at 360 min). Some arrive after the 30-minute attribution tail and
  are counted as organic by the pipeline.
- **Call centre and TV call-in.** Baseline call-centre orders convert contacts at 0.2;
  per-segment call-centre and TV call-in orders are 0.2 per segment minute × weight. These
  channels are outside the conversion metric.
- **Baskets and returns.** 17% of orders get 1-2 add-on lines for other products. Returns
  follow planted category rates (Fashion 28%, Jewellery 24%, Beauty 10%, Home & Living 8%,
  Wellness 7%, Electronics 6%; ×1.2 for premium and luxury price bands), arrive 1-21 days
  later, with reason probabilities size/fit 0.4, changed mind 0.25, not as described 0.2,
  damaged 0.1, late delivery 0.05.
- **Resulting scale.** Conversion is about 1.6% off air and 3.6% on air.

Flags: `--out --start --days --seed --products --presenters --base-conversion
--onair-uplift --halo --order-seq-start --segment-seq-start --dirty --dirty-rate --quiet`.

**Manifest and ground truth.** `_batch_manifest.json` has a `planted` block with every
parameter above and an `expected` block computed from this batch's schedule and traffic:
`expected_measured_lift` (what the z-test should measure), `lift_on_air_minutes`,
`channel_order_share` and `incremental_share_outside_window`. `_truth_order_attribution.csv`
gives `order_line_id`, `true_segment_id` and `order_source` (`baseline`, `airtime`, or
`addon` with segment `-1`). Neither is loaded: `load_to_snowflake.py` loads only its
`RAW_TABLES` and asserts that no `_truth_*` file is among them.

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
(`--run-dbt`), wait `--interval` seconds. Other flags: `--out --iterations --batch-days
--start --seed --products --presenters --no-load`. Dimensions are generated from `--seed`
and `--start` so they stay the same across batches; the fact seed follows the window. Window
start and order/segment ids continue from the previous batch's manifest.

## 4. Staging

One view per source, 1:1: cast types, `trim`/`lower` text, and dedupe with `row_number()`
keeping the latest load per natural key. No joins across entities.

- `stg_products` keeps every source version (`product_id`, `product_version`) with its
  `valid_from_date` / `valid_to_date`.
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

- **`dim_product`** is an event-time SCD2: one row per source version from `stg_products`,
  surrogate key on (`product_id`, `product_version`), valid over `[valid_from_ts,
  valid_to_ts)` as dated by the source. The current version runs to 9999-12-31.
- Facts join to the version whose interval contains the event time (`fact_order_line` on
  `event_ts_utc`, `fact_airtime_segment` on `start_ts_utc`). The singular test
  `assert_order_lines_use_version_valid_at_event_time` checks every order line.
- **`dbt/snapshots/dim_product_snapshot.sql`** is a capture-time audit trail: it snapshots
  the current product row with the `check` strategy on `category`, `list_price_band`,
  `list_price`, `brand` and `product_name`, and `hard_deletes='invalidate'`. Its versions are
  dated by the run that saw the change, so it records what the warehouse knew and when. The
  dimension does not read from it.

`snowflake/demo_scd2.sql` walks through the snapshot: query product 1's snapshot rows,
change its category and price in RAW, run `dbt run --select stg_products` and
`dbt snapshot`, and query again to see the old row closed and a new current one opened.

Only product is SCD2; presenter and show are SCD1 (D4 in DECISIONS.md).

## 6. Attribution

`int_orders_attributed_to_airtime` (a view) tags each order line with a segment:

1. **Candidates:** segments for the same product whose window
   `[start_ts_utc, end_ts_utc + attribution_lag_minutes)` contains `event_ts_utc`. The lag is a
   dbt var (30) and must match `LAG_MINUTES` in the generator and `analysis/offline_rules.py`.
2. **Tie-break:** latest start wins (last touch), then highest segment id.
3. **No match:** `attributed_segment_id = -1`, `is_airtime_attributed = false` (organic).

The dbt unit test `attribution_window_boundaries_and_tie_break` (in `_intermediate.yml`)
feeds seven hand-made order lines: before the start (organic), exactly at the start
(attributed), inside two open windows (latest start wins), exactly at end + 30 (organic,
twice), another product on air (organic), and the last second of the tail (attributed). Two
singular tests also guard it on real data: no order is attributed to a segment that started
after it, and every attributed order falls inside its segment's window.

Against the generator's ground truth, the 30-minute window gives 96.3% precision and 87.7%
recall on the sample batch. Recall is below 100% because the planted lag has a long tail
(section 10).

## 7. Facts and dimensions

| Model | Grain | Notes |
|---|---|---|
| `fact_order_line` | one row per order line | incremental merge; money measures; point-in-time `product_key` |
| `fact_airtime_segment` | one row per segment | duration, daypart and prime-time flag from the start hour; product version valid at start |
| `fact_returns` | one row per returned line | carries the sale's product, channel and segment, plus `sold_qty` |
| `fact_traffic` | one row per channel per minute | viewers and web sessions |
| `dim_date` | one row per day | `dbt_utils.date_spine`, clipped to the dates in the data |
| `dim_time_minute` | one row per minute of day (1,440) | daypart: morning 05-11, afternoon 12-17, prime time 18-22, late night otherwise |
| `dim_product` | one row per product version | event-time SCD2 (section 5) |
| `dim_presenter`, `dim_show` | one row per id | SCD1 |
| `dim_channel` | one row per channel | from the seed |

Daypart and the prime-time flag are defined once in `dbt/macros/daypart.sql` (`daypart`,
`is_prime_time`) and used by `dim_time_minute`, `fact_airtime_segment` and
`mart_conversion_windows`. Prime time 18:00-22:59 UTC matches the generator.

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
| `mart_segment_performance` | one row per segment | attributed orders, units, revenue, revenue per broadcast minute, revenue per window minute |
| `mart_conversion_windows` | airtime state × date × channel × daypart | online orders and web sessions on air vs off air |

Ratios (AOV, return rate, attributed share) are computed in Power BI from the summed base
measures, so they stay correct at any level of aggregation.

**Segment revenue.** Attributed revenue covers the whole attribution window: the airtime plus
the 30-minute tail. `revenue_per_broadcast_minute` = attributed revenue ÷ on-air minutes
(`segment_duration_minutes`); `revenue_per_window_minute` = attributed revenue ÷ (on-air
minutes + 30). The first is what a minute of airtime earns; the second spreads the same
revenue over the window it was collected in.

**Conversion definition.** Online channels only (Web Shop, Mobile App), for both orders and
sessions. A minute is `on_air` if it falls inside any segment's window
`[start, end + 30 min)`, otherwise `off_air`; orders take the state of their order minute and
are counted as distinct `order_id`. This rule is about broadcast time, not the product, and
is separate from `is_airtime_attributed` on `fact_order_line`, which is what attributed revenue
uses. Sum `orders` and `web_sessions` before dividing.

`_exposures.yml` declares the Power BI report ("Live Commerce Airtime Dashboard") as a dbt
exposure on the marts, facts and dimensions, so it appears in the `dbt docs` lineage graph.

## 9. Tests

`dbt ls` counts 24 models, 190 data tests, 1 unit test, 1 snapshot and 2 seeds.

- **Generic tests** in the YAML files: `unique`, `not_null`, `accepted_values`,
  `relationships` and `dbt_utils` range checks, plus uniqueness of the product version key.
- **Unit test:** `attribution_window_boundaries_and_tie_break`, seven cases (section 6).
- **Singular tests** in `dbt/tests/` (pass when they return no rows):

| Test | Checks |
|---|---|
| `assert_no_future_attribution` | no order attributed to a segment that started after it |
| `assert_attribution_within_lag_window` | every attributed order is inside `[start, end + lag)` |
| `assert_return_rate_within_bounds` | returned quantity never exceeds sold quantity per line |
| `assert_no_valid_row_has_null_keys` | no VARIANT row marked valid has missing or out-of-range fields |
| `assert_no_overlapping_segments_per_channel` | no two segments on a channel overlap in `[start, end)` |
| `assert_order_lines_use_version_valid_at_event_time` | every order line joins to the product version valid at its event time |

`dbt build` runs models and their tests in DAG order; a failing test fails the build.

**pytest** (`tests/`, 22 tests, `python -m pytest -q`): z-test values against hand-computed
numbers, guards for untestable inputs and CI behaviour; generator determinism, no
overlapping segments, show categories, dirty mode touching only the JSON feed, multi-line
orders, version pricing, the truth file covering every line and never being loaded, the
manifest's planted block, and id continuity across live batches; small null and recovery
checks, attribution precision/recall and window behaviour, and the offline attribution rules.

## 10. Statistics and validation

`analysis/offline_rules.py` re-implements the dbt attribution and conversion rules on the
generated CSVs, so the z-test and the validation run without Snowflake. It must be kept in
sync with `int_orders_attributed_to_airtime` and `mart_conversion_windows`.

`analysis/conversion_lift_test.py` runs a one-sided two-proportion z-test:
H0 on-air conversion equals off-air, H1 on-air is higher. It uses only the standard library.

- `--in generator/output` recomputes the counts from the CSVs with the offline rules.
- `--from-snowflake` sums `marts.mart_conversion_windows` by state, connecting as
  `SNOWFLAKE_BI_ROLE` / `SNOWFLAKE_BI_WAREHOUSE` (default `ROLE_BI_READER` / `WH_BI`).
- `--alpha` sets the significance level (default 0.05); CIs are at `1 − alpha`.
- `--bootstrap-days REPS` adds a percentile CI for the relative lift from resampling whole
  days (offline only), which relaxes the independent-sessions assumption.

It prints both rates, the absolute lift (per 1,000 sessions) and relative lift with CIs
(Wald for the difference, log ratio for the relative lift), z and p, and, on generated data,
the planted lift from the manifest with whether it falls inside the CI. Sessions are treated
as independent trials, so the result is a population-level lift, not a per-user causal
effect.

Reproducible result:

```bash
python generator/generate_batch.py --out generator/output --days 5 --seed 42
python analysis/conversion_lift_test.py --in generator/output
```

| | Orders | Sessions | Conversion |
|---|---|---|---|
| On air | 5,516 | 153,616 | 3.59% |
| Off air | 1,256 | 79,193 | 1.59% |

Relative lift: planted +119.7%, measured +126.4% (95% CI +113.1% to +140.6%), z = 27.27.

`analysis/validate_against_truth.py` compares measured against planted values:

- `--in DIR`: parameter recovery table (lift, tier ratios, prime weight, channel order shares,
  return rate per category, each with a 95% CI), attribution precision and recall against
  the truth file, and a window sweep over 15, 30, 45, 60 and 90 minutes.
- `--experiments` (with `--seeds N`, `--halo-seeds N`, `--days`): a null test with
  `--onair-uplift 0`, a seed sweep with CI coverage, and a halo check.
- `--markdown PATH` writes the results; `docs/VALIDATION.md` is produced with
  `--experiments --seeds 50 --markdown docs/VALIDATION.md`.

Summary (details in [docs/VALIDATION.md](docs/VALIDATION.md)): on the sample batch 13 of 14
recovery checks fall inside their 95% CI (Beauty return rate is the miss; about 0.7 misses
are expected by chance). Over 50 batches with no planted uplift, 4% are significant at
α = 0.05. Over 50 seeds the CI covers the planted lift 98% of the time. Across halo values
from 1.0 to 5.0 the measured lift tracks the planted one.

## 11. CI/CD

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`, one run at a time:

- `parse` (always): `dbt parse` offline, `python -m pytest -q`, then a 2-day generator run,
  the z-test and `validate_against_truth.py` as a smoke test.
- `check-secrets`: detects whether `SNOWFLAKE_ACCOUNT` is configured.
- `build-and-test` (only with secrets): key-pair auth, load a 2-day batch into RAW with
  `--truncate`, `drop_ci_schemas`, `dbt build --target ci --full-refresh` into `_CI` schemas
  (`STAGING_CI`, `MARTS_CI`, `SNAPSHOTS_CI`, ...), `sqlfluff lint` (blocking), and
  `dbt source freshness` (report only).

Without secrets `build-and-test` is skipped, so the CI badge then covers the offline checks
only. The profile target comes from `DBT_TARGET` (default `dev`, password auth; `ci` uses a
private key). The CI user needs `ROLE_LOADER` and `ROLE_TRANSFORMER`. CI reloads the shared
RAW schema, so it should point at its own database or account. Setup steps:
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
The report reads the dashboard dataset, June to August 2026
(`python generator/generate_batch.py --out generator/output --start 2026-06-01 --days 92 --seed 42`); figures below are
from that dataset and match the marts queried directly.

On generated data the pages show planted parameters coming back through the pipeline. They
are a check on the pipeline, not business findings.

**Overview** (slicer: month)
- Cards: €257 revenue per broadcast minute, €19.06M net revenue after returns, €23.82M GMV,
  13.69% return rate (units).
- Net revenue by month (June, July, August) with airtime-attributed revenue as a band.
- Revenue by channel. The order mix matches `planted.expected.channel_order_share`: planted
  40.4 / 25.8 / 25.8 / 8.1% (Web Shop / Mobile App / Call Centre / TV Linear), measured
  40.5 / 25.8 / 25.9 / 7.9%.
- Top products by net revenue with their return rates alongside.

**Airtime** (slicer: presenter experience tier)
- Airtime-attributed revenue, airtime share and broadcast minutes.
- Revenue by presenter, coloured by tier. Tier multipliers are planted at 1.6 (star),
  1.25 (established) and 1.05 (rookie). Revenue per broadcast minute comes out star €307,
  established €249, rookie €239. The gaps are smaller than the multipliers because the
  baseline-free share of segment revenue also depends on price and time slot; the clean
  recovery of the ratios is in `docs/VALIDATION.md`.
- Revenue per broadcast minute by daypart: prime time €378 vs €145-161 elsewhere, the
  planted 1.6× prime weight combined with 2.1× prime-time traffic.
- Category × presenter heatmap. Each show draws from fixed categories and each airing has
  one presenter, so a presenter's category mix reflects which shows they happened to host.
  The generator plants no presenter-category effect.

**Conversion** (on-air vs off-air split throughout)
- On-air vs off-air online conversion: 3.59% vs 1.64%, lift +119.0% (95% CI +116.0% to
  +122.0%) against a planted +115.6%.
- Conversion by daypart and by channel for each state.
- Orders and web sessions by state, to show the lift is a rate change and not just more
  traffic.

**Returns** (slicer: price band)
- Return rate, refunded value and units returned.
- Return rate by category (units): Fashion 26.3%, Jewellery 23.1%, Beauty 9.9%, Home &
  Living 7.7%, Wellness 6.6%, Electronics 5.5%. Planted line-level rates are 28 / 24 / 10 /
  8 / 7 / 6%; unit rates sit a little lower because partial returns are allowed.
- Reasons: size/fit 40.0%, changed mind 24.9%, not as described 19.9%, damaged 10.1%, late
  delivery 5.1% (planted 40 / 25 / 20 / 10 / 5%).
- Category × price band: premium and luxury bands are planted at 1.2× the category rate.

## 14. Status

- Run end to end on a Snowflake trial account on 25 October 2026 with the current code:
  dashboard dataset loaded, `dbt build --full-refresh` passing all 218 nodes, sqlfluff clean,
  and a green CI `build-and-test` run.
- Checked on Snowflake: mart z-test counts identical to the offline rules; dirty-batch
  quarantine counts equal the manifest; incremental runs pick up late-loaded rows without
  duplicates; the snapshot captures an in-place change.
- Offline: 22 pytest tests and the validation harness (`docs/VALIDATION.md`).
- Lineage and catalog, including the dashboard exposure: [dbt docs](https://utkarshsawant65.github.io/Live-commerce-airtime-intelligence/).

Limitations and next steps: end of [DECISIONS.md](DECISIONS.md).

## 15. File index

| Path | Purpose |
|---|---|
| `README.md`, `PROJECT_GUIDE.md`, `DECISIONS.md` | Overview, this guide, design decisions |
| `docs/KPI_CATALOG.md`, `docs/CI_SETUP.md` | KPI definitions; CI setup |
| `docs/VALIDATION.md` | Validation report, generated by `analysis/validate_against_truth.py` |
| `requirements.txt` | numpy, snowflake-connector-python, dbt-snowflake ≥1.10 with dbt-core <1.13, sqlfluff + dbt templater, python-dotenv, pytest |
| `.env.example`, `load_env.ps1` | Snowflake credential template; loads `.env` into PowerShell (bash: `set -a; source .env; set +a`) |
| `.sqlfluff`, `.pre-commit-config.yaml` | Lint config; local hooks |
| `.gitignore`, `.gitattributes` | Keeps secrets, keys, generated data and `.pbix` out; LF line endings |
| `generator/generate_batch.py` | Synthetic batch generator, planted parameters, manifest, truth file, `--dirty` mode |
| `generator/load_to_snowflake.py` | `PUT` + `COPY INTO` RAW; VARIANT load for JSON; never loads `_truth_*` |
| `generator/run_live.py` | Micro-batch loop, optional `dbt build` |
| `snowflake/00_setup.sql` | Database, schemas, warehouses, roles, grants, stage, file formats |
| `snowflake/demo_scd2.sql` | Snapshot (audit trail) walkthrough |
| `dbt/dbt_project.yml` | Schema routing, materialisations, vars `attribution_lag_minutes` (30) and `incremental_lookback_minutes` (120) |
| `dbt/packages.yml`, `dbt/package-lock.yml` | `dbt_utils` |
| `dbt/profiles.example.yml` | `dev` (password) and `ci` (key-pair) targets, chosen by `DBT_TARGET` |
| `dbt/macros/generate_schema_name.sql` | Unprefixed schema names plus `DBT_SCHEMA_SUFFIX` |
| `dbt/macros/surrogate_key.sql` | Wrapper over `dbt_utils.generate_surrogate_key` |
| `dbt/macros/daypart.sql` | `daypart` and `is_prime_time`, defined once |
| `dbt/macros/drop_ci_schemas.sql` | Drops `_CI` schemas; no-op outside the `ci` target |
| `dbt/seeds/seed_channel.csv` | Channel reference data |
| `dbt/seeds/category_return_baseline.csv` | Baseline return rate per category |
| `dbt/models/staging/` | `_sources.yml`, `_staging.yml` and ten `stg_*` models |
| `dbt/snapshots/dim_product_snapshot.sql` | Product snapshot (capture-time audit trail) |
| `dbt/models/intermediate/` | `int_orders_attributed_to_airtime` and its YAML with the unit test |
| `dbt/models/marts/dimensions/` | Six dimensions and `_dimensions.yml` |
| `dbt/models/marts/facts/` | Four facts and `_facts.yml` |
| `dbt/models/marts/` | Three metric marts, `_marts.yml`, `_exposures.yml` |
| `dbt/tests/` | Six singular tests, including `assert_no_overlapping_segments_per_channel` and `assert_order_lines_use_version_valid_at_event_time` |
| `analysis/offline_rules.py` | Offline copy of the dbt attribution and conversion rules |
| `analysis/conversion_lift_test.py` | Two-proportion z-test with CIs and optional day bootstrap, offline or from Snowflake |
| `analysis/validate_against_truth.py` | Parameter recovery, attribution vs truth, null test, seed sweep, halo check |
| `tests/` | pytest suite: `conftest.py`, `test_generator.py`, `test_ztest.py`, `test_validation.py` |
| `.github/workflows/ci.yml` | CI workflow |
| `streaming/` | Streams + Tasks and Dynamic Table scripts, README |
