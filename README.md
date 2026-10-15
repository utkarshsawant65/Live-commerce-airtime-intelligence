# Live Commerce — Airtime Intelligence Platform

[![CI](https://github.com/utkarshsawant65/Live-commerce-airtime-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarshsawant65/Live-commerce-airtime-intelligence/actions/workflows/ci.yml)
![dbt](https://img.shields.io/badge/dbt-1.8%2B-FF694B?logo=dbt&logoColor=white)
![Snowflake](https://img.shields.io/badge/Snowflake-29B5E8?logo=snowflake&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-F2C811?logo=powerbi&logoColor=black)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)

A governed, tested **dbt + Snowflake + Power BI** data product that ties every order to the
**broadcast airtime segment** that drove it, and measures live-commerce performance:
**revenue per broadcast minute**, **conversion lift**, and **return-rate intelligence** by
presenter, product and channel.

---

## What it is

Live-commerce retailers sell via live broadcast (TV + social) backed by an online shop and
app. The defining mechanic: a product shown on air by a presenter in a time window drives a
simultaneous sales spike across every channel (call-centre, web, app). This project models
that mechanic end to end.

A synthetic but realistically correlated stream of broadcast airtime, cross-channel orders,
returns and minute-level viewership is landed in Snowflake, then cleaned and modelled with dbt
into a **star schema** (SCD2 product history, conformed dimensions). An attribution layer ties
every order to the airtime segment that drove it. A governed **metrics mart** defines each KPI
once; a **Power BI** model with **row-level security** turns it into self-service dashboards.
The whole thing is engineered like software — incremental models, dbt tests written test-first,
a Git branch strategy, and GitHub Actions CI that runs the full build on every pull request.

> **Why synthetic data?** No public dataset carries a broadcast/airtime dimension. Generating
> it means the airtime→sales correlation is known, so the analytics produce visible, testable
> signal and the attribution logic can be verified against ground truth.

## Questions it answers

| Business question | Answered by |
|---|---|
| Which airtime segments (product × presenter × time window) earn the most per broadcast minute? | `mart_segment_performance` → **revenue per broadcast minute** |
| Does going on air actually lift conversion — and is it statistically significant? | `mart_conversion_windows` + a **two-proportion z-test** |
| Which products, presenters and categories drive the highest return rates? | `fact_returns` + **return rate** KPI |
| What is net revenue after returns by show, presenter and channel? | `mart_kpi_metrics` — one governed definition |

## Architecture

```
[Python generator] ──CSV/JSON──▶ [Snowflake stage] ──COPY INTO──▶ RAW
        │ (batch, then looped for "simulated live")
        ▼
     RAW  →  STAGING  →  INTERMEDIATE  →  MARTS  →  Power BI
           (1:1 clean)  (attribution)   (facts +   (star model,
                                         dims +      measures, RLS)
                                         metrics)
```

**Layering rule:** BI reads from `MARTS` only. No dashboard or ad-hoc query ever touches
`STAGING` or `RAW`. This is what guarantees consistent KPIs.

## Data model (star schema)

**Dimensions** — `dim_date`, `dim_time_minute` (prime-time flag, daypart), `dim_product`
(**SCD2**, point-in-time), `dim_presenter` (SCD1), `dim_channel`, `dim_show`. Every dimension
carries a surrogate key and an unknown member (`-1`) so fact joins never silently drop rows.

**Facts** — `fact_order_line` (one row per product line per order, **incremental**),
`fact_airtime_segment` (one row per broadcast segment), `fact_returns` (one row per returned
line), `fact_traffic` (one row per channel per minute — the conversion denominator).

**Attribution** — `int_orders_attributed_to_airtime` matches each order to the airtime segment
for that product whose window `[start, end + lag]` contains the order time, applying a
last-touch tie-break for overlaps and tagging unmatched orders as organic (segment `-1`).

**Metrics marts** — `mart_kpi_metrics` (additive base measures), `mart_segment_performance`
(revenue per broadcast minute), `mart_conversion_windows` (on-air vs off-air conversion).

## Dashboard

A four-page Power BI report built on the marts only:

| Page | Focus |
|---|---|
| **Overview** | Net revenue after returns, GMV, revenue per broadcast minute, channel mix |
| **Airtime** | Presenter and segment ROI, revenue per minute by daypart, category × presenter heatmap |
| **Conversion** | On-air vs off-air conversion, lift by daypart, z-test significance |
| **Returns** | Return rate by category, reason code and price band; refunded value |

On a generated dataset, airtime drives roughly two-thirds of revenue and a statistically
significant conversion lift (p < 0.01), with prime time the most productive daypart and returns
concentrated in Fashion and Jewellery. The `.pbix` file is not versioned; see
[`PROJECT_GUIDE.md` §18](PROJECT_GUIDE.md#18-dashboard-insights--the-story-each-chart-tells)
for a page-by-page walkthrough.

## Repository layout

| Path | Purpose |
|---|---|
| `generator/` | Python synthetic data generator, Snowflake loader, and "simulated live" loop |
| `snowflake/` | `00_setup.sql` (database, schemas, warehouses, RBAC, stage) and an SCD2 demo script |
| `dbt/` | dbt-core project: staging → intermediate → marts, snapshots, tests, macros, seeds |
| `analysis/` | Two-proportion z-test on conversion lift |
| `streaming/` | Snowflake-native real-time upgrade path (Streams + Tasks, Dynamic Tables) |
| `.github/workflows/` | CI: `dbt parse` + sqlfluff, then `dbt build` / test / freshness on Snowflake |
| `docs/KPI_CATALOG.md` | One definition per KPI (formula • grain • filters) |
| `docs/CI_SETUP.md` | One-time CI setup: key-pair auth, GitHub Secrets, branch protection |
| `DECISIONS.md` | Architecture decisions, the alternatives rejected, and how strongly each holds |
| `PROJECT_GUIDE.md` | Full walkthrough: business case, data flow, file-by-file index |

## Quick start

The generator and the z-test run fully offline; Snowflake is needed only for loading and dbt.

```bash
# 1. Python environment (3.12)
python -m venv .venv
source .venv/Scripts/activate        # Git Bash; .venv\Scripts\activate on cmd/PowerShell,
                                     # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# 2. Generate a batch of synthetic data (add --dirty to inject realistic bad rows)
python generator/generate_batch.py --out generator/output --days 5 --seed 42

# 3. Run the conversion-lift z-test offline on the generated CSVs
python analysis/conversion_lift_test.py --in generator/output
```

With a Snowflake account:

```bash
# 4. Provision the platform: run snowflake/00_setup.sql in a worksheet as ACCOUNTADMIN
#    (replace <YOUR_USER> in section 4 first).

# 5. Configure credentials
cp .env.example .env                              # fill in your account / user / password
cp dbt/profiles.example.yml dbt/profiles.yml      # gitignored
#    Load .env into your shell (PowerShell: `. .\load_env.ps1`; bash: `set -a; source .env; set +a`)

# 6. Load RAW and build everything (models + tests)
python generator/load_to_snowflake.py --in generator/output --truncate
cd dbt && dbt deps && dbt build --profiles-dir .

# Optional: simulated live micro-batches with incremental dbt after each load
python generator/run_live.py --iterations 6 --interval 60 --run-dbt
```

## Engineering practices

- **Test-first data quality** — generic dbt tests (`unique`, `not_null`, `accepted_values`,
  `relationships`, ranges) plus singular tests for business invariants (return rate ≤ 1, no
  future attribution, attribution inside the lag window, no dirty row passes as valid).
- **Resilient ingestion** — a VARIANT (schema-on-read) landing path with defensive parsing and a
  quarantine model, exercised by the generator's `--dirty` mode.
- **Source freshness** — `dbt source freshness` alerts on a stalled feed.
- **Incremental modelling** — `fact_order_line` uses a `merge` strategy with a lookback window
  that re-attributes late-arriving orders without rebuilding.
- **Governance** — Snowflake RBAC (functional roles, separated warehouses) + Power BI RLS.
- **CI/CD** — GitHub Actions with key-pair auth builds and tests every PR in isolated `_CI`
  schemas with `--full-refresh`.

## Status

- [x] Foundations — scaffold, Snowflake setup, dbt skeleton
- [x] Generate + land — Python generator with airtime→sales correlation
- [x] Staging — 1:1 cleaned/typed/deduped models, sources + freshness, generic tests
- [x] Dimensions — conformed dims, `date_spine`, SCD2 snapshot on `dim_product`
- [x] Intermediate + facts — attribution model, incremental `fact_order_line`, singular tests
- [x] Marts + metrics + stats — `mart_kpi_metrics`, KPI catalog, the z-test
- [x] CI/CD — GitHub Actions against Snowflake (24 models, 197 tests green)
- [x] "Live" + docs — generator loop, streaming upgrade path, `dbt docs` lineage with exposure
- [x] Power BI — star model, measures, RLS, four-page dashboard

Ideas for further work are listed at the end of [`DECISIONS.md`](DECISIONS.md).

## License

[MIT](LICENSE)
