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
Power BI report with row-level security reads the marts.

The data is synthetic because no public dataset has a broadcast/airtime dimension. Since the
generator builds in a known airtime effect, the attribution and the lift test can be checked
against it.

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
      (clean 1:1)   (attribution)           SNAPSHOTS (SCD2 product)
```

Power BI reads `MARTS` only; nothing downstream queries `RAW` or `STAGING`.

## Data model

- **Dimensions:** `dim_date`, `dim_time_minute` (daypart, prime-time flag), `dim_product`
  (SCD2), `dim_presenter`, `dim_show`, `dim_channel`. All have surrogate keys; product,
  presenter, show and channel also have an `Unknown` member so fact joins never drop rows.
- **Facts:** `fact_order_line` (one row per order line, incremental), `fact_airtime_segment`
  (one row per segment), `fact_returns` (one row per returned line), `fact_traffic` (one row
  per channel per minute).
- **Attribution:** `int_orders_attributed_to_airtime` matches each order line to a segment for
  the same product whose window `[start, end + 30 min)` contains the order time. Overlaps go
  to the latest start; unmatched orders get segment `-1` (organic).
- **Marts:** `mart_kpi_metrics` (additive base measures), `mart_segment_performance`
  (revenue per broadcast minute), `mart_conversion_windows` (online orders and sessions
  on air vs off air, by date, channel and daypart).

KPI definitions: [docs/KPI_CATALOG.md](docs/KPI_CATALOG.md).

## Results (sample run)

On the 5-day sample batch from the quick start (`--days 5 --seed 42`), online conversion is
0.026% on air vs 0.014% off air (4,653 orders / 18.2M sessions vs 1,309 / 9.7M), a relative
lift of +89.0% (z = 20.69, one-sided p ≈ 2e-95).

## Dashboard

Four Power BI pages built on the marts:

| Page | Content |
|---|---|
| Overview | Net revenue after returns, GMV, revenue per broadcast minute, channel mix |
| Airtime | Revenue by presenter and tier, revenue per minute by daypart, category × presenter |
| Conversion | On-air vs off-air conversion, by daypart, with the z-test result |
| Returns | Return rate by category, reason and price band; refunded value |

The `.pbix` is not in the repo. The [project guide](PROJECT_GUIDE.md#13-dashboard) describes
each page.

## Repository layout

| Path | Contents |
|---|---|
| `generator/` | Data generator, Snowflake loader, micro-batch loop |
| `snowflake/` | `00_setup.sql` (database, schemas, warehouses, roles, stage) and `demo_scd2.sql` |
| `dbt/` | Staging, intermediate and mart models, snapshot, seeds, macros, tests |
| `analysis/` | Two-proportion z-test for conversion lift |
| `streaming/` | Optional Streams + Tasks and Dynamic Table scripts |
| `.github/workflows/ci.yml` | CI: offline parse and smoke test; Snowflake build, tests and lint |
| [docs/KPI_CATALOG.md](docs/KPI_CATALOG.md) | KPI formulas, grains and filters |
| [docs/CI_SETUP.md](docs/CI_SETUP.md) | Key-pair auth, GitHub secrets, branch protection |
| [DECISIONS.md](DECISIONS.md) | Design decisions and the alternatives considered |
| [PROJECT_GUIDE.md](PROJECT_GUIDE.md) | Layer-by-layer walkthrough and file index |

## Quick start

The generator and the z-test run offline. Snowflake is only needed to load data and run dbt.

```bash
# 1. Python 3.12 environment
python -m venv .venv
source .venv/Scripts/activate      # Git Bash; PowerShell: .venv\Scripts\Activate.ps1; macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Generate a 5-day batch (add --dirty to corrupt the JSON order-line feed)
python generator/generate_batch.py --out generator/output --days 5 --seed 42

# 3. Run the conversion-lift z-test on the generated CSVs
python analysis/conversion_lift_test.py --in generator/output
```

With a Snowflake account:

```bash
# 4. Run snowflake/00_setup.sql as a user with ACCOUNTADMIN. Edit and uncomment section 4
#    to grant role_loader, role_transformer and role_bi_reader to your user.

# 5. Credentials
cp .env.example .env                            # fill in account, user, password
cp dbt/profiles.example.yml dbt/profiles.yml    # gitignored
set -a; source .env; set +a                     # PowerShell: . .\load_env.ps1

# 6. Load RAW, then build and test everything
python generator/load_to_snowflake.py --in generator/output --truncate
cd dbt && dbt deps && dbt build --profiles-dir . && cd ..

# 7. Optional: z-test on the mart instead of the CSVs (uses ROLE_BI_READER / WH_BI)
python analysis/conversion_lift_test.py --from-snowflake

# 8. Optional: micro-batch loop: append a 1-day batch to RAW every 60 s, then dbt build
python generator/run_live.py --iterations 6 --interval 60 --run-dbt
```

`run_live.py` continues from the last batch's manifest in `generator/output` (next window,
next order and segment ids). Its `--seed` (default 42, same as step 2) fixes the dimensions,
so products and presenters stay identical to the initial load.

## Engineering practices

- **Tests:** 185 data tests, made up of generic tests (`unique`, `not_null`, `accepted_values`,
  `relationships`, ranges) plus singular tests for no future attribution, attribution inside
  the lag window, returns never exceeding sales, and no invalid row passing as valid.
- **Semi-structured ingestion:** the JSON order-line feed lands as `VARIANT`, is parsed with
  `try_cast`, and failing rows go to a quarantine model with a reason. `--dirty` exercises it.
- **Incremental model:** `fact_order_line` merges on `order_line_id` and reprocesses a
  120-minute lookback plus anything loaded since the last run, so late rows are picked up.
- **Freshness:** `dbt source freshness` thresholds on every RAW table.
- **Access:** functional Snowflake roles and separate load/transform/BI warehouses; RLS in
  Power BI.
- **CI:** GitHub Actions on pull requests to `main`: offline `dbt parse` and a generator/z-test
  smoke test, then (if Snowflake secrets are set) a full `dbt build` in `_CI` schemas with
  key-pair auth, and `sqlfluff` lint.

## Status

The pipeline has been built and run end to end on Snowflake: generator, load, 24 dbt models,
1 snapshot, 2 seeds and 185 tests, CI, and the four-page Power BI report. Known limitations
and next steps are at the end of [DECISIONS.md](DECISIONS.md).

## License

[MIT](LICENSE)
