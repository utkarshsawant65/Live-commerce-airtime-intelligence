# Live Commerce Airtime Intelligence Platform

[![CI](https://github.com/utkarshsawant65/Live-commerce-airtime-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarshsawant65/Live-commerce-airtime-intelligence/actions/workflows/ci.yml)
![dbt](https://img.shields.io/badge/dbt-1.10%2B-FF694B?logo=dbt&logoColor=white)
![Snowflake](https://img.shields.io/badge/Snowflake-29B5E8?logo=snowflake&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-F2C811?logo=powerbi&logoColor=black)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)

A dbt + Snowflake + Power BI data product for live commerce. It attributes each order to the
broadcast airtime segment that drove it and reports revenue per broadcast minute, on-air
conversion lift and return rates by presenter, product and channel.

## What it is

Live-commerce retailers sell through live broadcast (TV and streams) alongside a web shop,
app and call centre. When a presenter features a product on air, orders for that product
rise across every channel. This project models that end to end.

A Python generator produces correlated airtime, order, return and minute-level traffic data.
It is loaded into Snowflake and modelled with dbt into a star schema: conformed dimensions,
an SCD2 product dimension, an attribution model, and marts that define each KPI once. A
four-page Power BI report reads the marts.

The data is synthetic because no public dataset has a broadcast/airtime dimension. The
generator plants named parameters (conversion rates, presenter-tier and prime-time weights,
return rates, response lag) and records them, so what the pipeline measures can be checked
against known values. See [Validation](#validation).

## Questions it answers

| Question | Answered by |
|---|---|
| Which segments (product × presenter × time window) earn the most per broadcast minute? | `mart_segment_performance` |
| Does being on air lift online conversion, and is the lift significant? | `mart_conversion_windows` + a two-proportion z-test |
| Which products, presenters and categories have the highest return rates? | `fact_returns`, `mart_kpi_metrics` |
| What is net revenue after returns by show, presenter and channel? | `mart_kpi_metrics` |

## Architecture

```
generator (Python) --CSV/JSON--> Snowflake stage --COPY INTO--> RAW
                                                                 |
                                                                dbt
                                                                 v
        STAGING  ->  INTERMEDIATE  ->  MARTS (facts, dims, metric marts)  ->  Power BI
      (clean 1:1)   (attribution)           SNAPSHOTS (product audit trail)
```

Power BI reads `MARTS` only; nothing downstream queries `RAW` or `STAGING`.

## Data model

- **Dimensions:** `dim_date`, `dim_time_minute` (daypart, prime-time flag), `dim_product`
  (SCD2), `dim_presenter`, `dim_show`, `dim_channel`. All have surrogate keys; product,
  presenter, show and channel also have an `Unknown` member so fact joins never drop rows.
- **Product history:** `dim_product` is an event-time SCD2 built from the source's validity
  dates (key: `product_id` + `product_version`); facts join to the version valid at their
  event time. The `dim_product_snapshot` snapshot is a separate capture-time audit trail. A
  singular test checks that every order line uses the version valid at its event time.
- **Facts:** `fact_order_line` (one row per order line, incremental), `fact_airtime_segment`
  (one row per segment), `fact_returns` (one row per returned line), `fact_traffic` (one row
  per channel per minute).
- **Attribution:** `int_orders_attributed_to_airtime` matches each order line to a segment for
  the same product whose window `[start, end + 30 min)` contains the order time. Overlaps go
  to the latest start; unmatched orders get segment `-1` (organic). A dbt unit test covers the
  window boundaries and the tie-break with seven hand-made cases.
- **Marts:** `mart_kpi_metrics` (additive base measures), `mart_segment_performance`
  (revenue per broadcast minute and per window minute), `mart_conversion_windows` (online
  orders and sessions on air vs off air, by date, channel and daypart).

KPI definitions: [docs/KPI_CATALOG.md](docs/KPI_CATALOG.md).

## Validation

The generator writes every planted parameter to `_batch_manifest.json` (`planted`), together
with the values the pipeline should measure given that batch's schedule and traffic
(`planted.expected`). It also writes the true segment behind each order line to
`_truth_order_attribution.csv`. Neither file is loaded into Snowflake: the loader loads only
its list of RAW tables and refuses `_truth_*` files. The pipeline measures the parameters
from the loaded data alone, and `analysis/validate_against_truth.py` compares the two, using
an offline copy of the dbt rules (`analysis/offline_rules.py`).

Parameter recovery on the sample batch (`--days 5 --seed 42`):

| Parameter | Planted | Measured | 95% CI |
|---|---|---|---|
| On-air conversion lift | +119.7% | +126.4% | +113.1% to +140.6% |
| Tier ratio star / established | 1.28 | 1.24 | 0.86 to 1.56 |
| Tier ratio rookie / established | 0.84 | 0.86 | 0.69 to 1.03 |
| Prime / non-prime segment weight | 1.60 | 1.47 | 1.28 to 1.71 |
| Order share Web Shop / Mobile App / Call Centre / TV Linear | 40.7 / 25.9 / 25.3 / 8.1% | 40.2 / 26.0 / 25.6 / 8.2% | all four inside their CI |
| Return rate, six categories | 6.3% to 29.1% | 6.1% to 28.7% | five inside; Beauty planted 10.9%, measured 12.3% (CI 10.9% to 13.9%) |

13 of the 14 checks fall inside their 95% interval; about 0.7 misses are expected by chance
with 14 intervals. The tier and prime intervals are wide on five days of data.

Experiments over freshly generated 5-day batches (`--experiments --seeds 50`):

- **Null test:** 50 batches with `--onair-uplift 0`: 4% significant at α = 0.05, mean
  measured lift +0.6% (sd 3.4%).
- **Seed sweep:** 50 batches: planted lift +115.3% on average, measured +116.0% (sd 8.8%,
  range +94.9% to +137.1%). The 95% CI contains the planted value in 98% of batches.
- **Halo check:** the halo multiplies online sessions while a segment window is open. It
  changes traffic volume, not conversion, and the measured lift follows the planted one
  (10 seeds per value):

| Halo | 1.0 | 1.5 | 2.0 | 2.9 (default) | 4.0 | 5.0 |
|---|---|---|---|---|---|---|
| Planted lift | +127.7% | +123.9% | +120.2% | +114.0% | +106.8% | +100.7% |
| Measured lift | +132.4% | +121.6% | +122.2% | +115.5% | +106.5% | +101.5% |

The planted value drifts with the halo because airtime orders that arrive after the
30-minute window count in the off-air baseline, more so when there is more on-air traffic.

Attribution against ground truth on the sample batch (window = minutes after segment end):

| Window | 15 | 30 (used) | 45 | 60 | 90 |
|---|---|---|---|---|---|
| Precision | 97.1% | 96.3% | 95.7% | 94.9% | 93.9% |
| Recall | 78.7% | 87.7% | 92.2% | 94.5% | 96.4% |

The response lag is planted as lognormal (median 9 min, long tail), so some airtime orders
arrive after the window and count as organic. A longer window catches them but also
attributes more organic orders of the same product.

Full tables: [docs/VALIDATION.md](docs/VALIDATION.md). To reproduce:

```bash
python generator/generate_batch.py --out generator/output --days 5 --seed 42
python analysis/validate_against_truth.py --in generator/output
python analysis/validate_against_truth.py --experiments --seeds 50 --markdown docs/VALIDATION.md
```

## Results (sample run)

On the 5-day sample batch, online conversion is 3.59% on air vs 1.59% off air (5,516 orders /
153,616 sessions vs 1,256 / 79,193). Relative lift: planted +119.7%, measured +126.4% (95% CI
+113.1% to +140.6%), z = 27.27, one-sided p ≈ 5e-164.

```bash
python generator/generate_batch.py --out generator/output --days 5 --seed 42
python analysis/conversion_lift_test.py --in generator/output
```

On the dashboard dataset (June to August, `--start 2026-06-01 --days 92 --seed 42`) the same test gives 96,069 /
2,677,192 vs 24,590 / 1,500,582, identical from Snowflake and offline: measured +119.0%
(95% CI +116.0% to +122.0%), planted +115.6%, just below the interval (about 2.2 standard
errors). Ten other 92-day seeds all contain their planted lift (z between -1.8 and +1.4,
mean -0.3), so this is chance rather than bias.

These numbers measure planted parameters; they are not findings about a real business.

## Dashboard

Four Power BI pages built on the marts. On generated data they show the planted parameters
coming back through the pipeline:

| Page | Content | Planted vs shown (dashboard dataset) |
|---|---|---|
| Overview | Net revenue after returns €19.06M, GMV €23.82M, €257 per broadcast minute, channel mix | order share planted 40.4 / 25.8 / 25.8 / 8.1% (web / app / call centre / TV), measured 40.5 / 25.8 / 25.9 / 7.9% |
| Airtime | Revenue by presenter and tier, revenue per minute by daypart, category × presenter | tier multipliers planted 1.6 / 1.25 / 1.05: revenue per minute star €307, established €249, rookie €239. Prime time €378 vs €145-161 per minute: the planted 1.6× prime weight on top of 2.1× prime-time traffic |
| Conversion | On-air vs off-air online conversion, by daypart | planted lift +115.6%, measured +119.0% (3.59% vs 1.64%) |
| Returns | Return rate by category, reason and price band; refunded value | reasons planted 40 / 25 / 20 / 10 / 5%, shown 40.0 / 24.9 / 19.9 / 10.1 / 5.1%. Category rates (by units) follow the planted order: Fashion 26.3%, Jewellery 23.1% … Electronics 5.5% |

The dataset is `python generator/generate_batch.py --out generator/output --start 2026-06-01 --days 92 --seed 42`. Unit-based
return rates sit a little below the planted line-level rates because partial returns are
allowed; the line-level check is in [docs/VALIDATION.md](docs/VALIDATION.md). The `.pbix`
is not in the repo; the [project guide](PROJECT_GUIDE.md#13-dashboard) describes each page.

## Repository layout

| Path | Contents |
|---|---|
| `generator/` | Data generator, Snowflake loader, micro-batch loop |
| `snowflake/` | `00_setup.sql` (database, schemas, warehouses, roles, stage) and `demo_scd2.sql` (snapshot walkthrough) |
| `dbt/` | Staging, intermediate and mart models, snapshot, seeds, macros, tests |
| `analysis/` | Two-proportion z-test, validation against the planted parameters, shared offline rules |
| `tests/` | pytest suite for the generator, z-test and validation |
| `streaming/` | Optional Streams + Tasks and Dynamic Table scripts |
| `.github/workflows/ci.yml` | CI: offline parse, pytest and smoke test; Snowflake build, tests and lint |
| [docs/KPI_CATALOG.md](docs/KPI_CATALOG.md) | KPI formulas, grains and filters |
| [docs/VALIDATION.md](docs/VALIDATION.md) | Generated validation report |
| [docs/CI_SETUP.md](docs/CI_SETUP.md) | Key-pair auth, GitHub secrets, branch protection |
| [DECISIONS.md](DECISIONS.md) | Design decisions and the alternatives considered |
| [PROJECT_GUIDE.md](PROJECT_GUIDE.md) | Layer-by-layer walkthrough and file index |

## Quick start

The generator, the z-test, the validation and the tests run offline. Snowflake is only
needed to load data and run dbt.

```bash
# 1. Python 3.12 environment
python -m venv .venv
source .venv/Scripts/activate      # Git Bash; PowerShell: .venv\Scripts\Activate.ps1; macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Generate a 5-day batch (add --dirty to corrupt the JSON order-line feed)
python generator/generate_batch.py --out generator/output --days 5 --seed 42

# 3. Conversion-lift z-test and validation on the generated CSVs
python analysis/conversion_lift_test.py --in generator/output
python analysis/validate_against_truth.py --in generator/output

# 4. Tests
python -m pytest -q
```

With a Snowflake account:

```bash
# 5. Run snowflake/00_setup.sql as a user with ACCOUNTADMIN. Edit and uncomment section 4
#    to grant role_loader, role_transformer and role_bi_reader to your user.

# 6. Credentials
cp .env.example .env                            # fill in account, user, password
cp dbt/profiles.example.yml dbt/profiles.yml    # gitignored
set -a; source .env; set +a                     # PowerShell: . .\load_env.ps1

# 7. Load RAW, then build and test everything
python generator/load_to_snowflake.py --in generator/output --truncate
cd dbt && dbt deps && dbt build --profiles-dir . && cd ..

# 8. Optional: z-test on the mart instead of the CSVs (uses ROLE_BI_READER / WH_BI)
python analysis/conversion_lift_test.py --from-snowflake

# 9. Optional: micro-batch loop: append a 1-day batch to RAW every 60 s, then dbt build
python generator/run_live.py --iterations 6 --interval 60 --run-dbt
```

`run_live.py` continues from the last batch's manifest in `generator/output` (next window,
next order and segment ids). Its `--seed` (default 42, same as step 2) fixes the dimensions,
so products and presenters stay identical to the initial load.

## Engineering practices

- **Tests:** 190 dbt data tests: generic tests (`unique`, `not_null`, `accepted_values`,
  `relationships`, ranges) plus six singular tests (no future attribution, attribution inside
  the lag window, returns never exceeding sales, no invalid row passing as valid, no
  overlapping segments per channel, order lines on the product version valid at event
  time). One dbt unit test with seven cases covers the attribution rules. 22 pytest tests
  cover the generator, the z-test and the validation.
- **Semi-structured ingestion:** the JSON order-line feed lands as `VARIANT`, is parsed with
  `try_cast`, and failing rows go to a quarantine model with a reason. `--dirty` exercises it.
- **Incremental model:** `fact_order_line` merges on `order_line_id` and reprocesses a
  120-minute lookback plus anything loaded since the last run, so late rows are picked up.
- **Freshness:** `dbt source freshness` thresholds on every RAW table.
- **Access:** functional Snowflake roles (BI reads `MARTS` only) and separate
  load/transform/BI warehouses.
- **CI:** GitHub Actions on pull requests and pushes to `main`. The `parse` job always runs
  offline `dbt parse`, `pytest`, and a generator / z-test / validation smoke test. The
  Snowflake job (`dbt build` in `_CI` schemas with key-pair auth, then `sqlfluff` lint) runs
  only when the Snowflake secrets are configured and is skipped otherwise. A green badge
  without secrets therefore covers the offline checks only.

## Status

The current code was run end to end on a Snowflake trial account on 25 October 2026:

- Dashboard dataset (`--start 2026-06-01 --days 92 --seed 42`: 182,203 orders, 228,886 lines, 2,912 segments)
  loaded with key-pair auth; `dbt build --full-refresh` passed all 218 nodes (24 models,
  190 data tests, 1 unit test, 1 snapshot, 2 seeds); `sqlfluff lint` clean.
- The z-test read from `mart_conversion_windows` gives exactly the same counts as the
  offline rules on the same batch.
- A `--dirty` batch: quarantine counts per reason equal the manifest (91 of each type).
- Incremental runs: two appended live batches, no duplicate `order_line_id`, and a row
  loaded late with an event time months in the past was picked up.
- The snapshot captured an in-place product change as a new version with a gap-free timeline.
- A green CI `build-and-test` run against the same account.

Lineage and catalog: [dbt docs](https://utkarshsawant65.github.io/Live-commerce-airtime-intelligence/). Offline, the same code passes 22 pytest tests and
the validation harness. Known limitations and next steps are at the end of
[DECISIONS.md](DECISIONS.md).

## License

[MIT](LICENSE)
