# KPI Catalog

Each KPI is defined once in the dbt marts and read by Power BI without being re-derived:
[`mart_kpi_metrics`](../dbt/models/marts/mart_kpi_metrics.sql),
[`mart_segment_performance`](../dbt/models/marts/mart_segment_performance.sql) and
[`mart_conversion_windows`](../dbt/models/marts/mart_conversion_windows.sql).

Conventions:
- Timestamps are UTC throughout the warehouse.
- Marts store additive base measures. Ratios are computed in BI from summed numerators and
  denominators, never stored pre-divided at a grain where they would not roll up.

## Base measures: `mart_kpi_metrics`

Grain: `event_date` × `product_key` × `presenter_key` × `channel_key` × `attributed_segment_id`.
The presenter is the attributed segment's presenter; organic orders get the unknown presenter.
Returns are aligned to the date and keys of the original sale.

| KPI | Column | Formula | Additivity |
|---|---|---|---|
| GMV | `gmv` | `Σ gross_amount` | additive |
| Discounts | `discounts` | `Σ discount_amount` | additive |
| Returns refund | `returns_refund` | `Σ refund_amount` | additive |
| Net revenue after returns | `net_revenue_after_returns` | `GMV − Discounts − Returns refund` | additive |
| Total net revenue | `total_net_revenue` | `Σ net_amount` (before returns) | additive |
| Airtime-attributed revenue | `airtime_attributed_revenue` | `Σ net_amount where is_airtime_attributed` | additive |
| Units sold | `units_sold` | `Σ quantity` | additive |
| Returned units | `returned_units` | `Σ returned_qty` | additive |
| Distinct orders | `distinct_orders` | `COUNT(DISTINCT order_id)` | not additive across products (a multi-product order appears in several rows) |

`is_airtime_attributed` comes from the attribution model: the order line matched a segment
for its own product within `[start, end + 30 min)`.

### Ratios (computed in BI)

| KPI | Formula | Notes |
|---|---|---|
| AOV | `Net revenue after returns ÷ Distinct orders` | guard against zero orders; distinct orders should be counted from `fact_order_line` when slicing across products |
| Return rate | `Returned units ÷ Units sold` | in `[0, 1]`; a singular test checks returns never exceed sales per line |
| Airtime revenue share | `Airtime-attributed revenue ÷ Total net revenue` | share of revenue tied to a segment |

## Revenue per broadcast minute: `mart_segment_performance`

Grain: one row per segment (`segment_id`).

| | |
|---|---|
| Formula | `attributed_revenue ÷ segment_duration_minutes` |
| Inputs | net revenue, orders and units of order lines attributed to the segment |
| Rolls up | sum `attributed_revenue` and `segment_duration_minutes` first, then divide (by presenter, product, show, daypart, date) |

## Conversion rate and lift: `mart_conversion_windows`

Grain: `airtime_state` × `event_date` × `channel_key` × `daypart`.

| | |
|---|---|
| Scope | online channels only (Web Shop, Mobile App), for both orders and sessions |
| On-air rule | a minute is `on_air` if it falls in any segment's window `[start, end + 30 min)`; otherwise `off_air`. Orders take the state of their order minute. |
| Orders | distinct `order_id` |
| Conversion rate | `Σ orders ÷ Σ web_sessions` per state (sum before dividing) |
| Absolute lift | `rate(on_air) − rate(off_air)` |
| Relative lift | `rate(on_air) ÷ rate(off_air) − 1` |
| Significance | one-sided two-proportion z-test in [`analysis/conversion_lift_test.py`](../analysis/conversion_lift_test.py), on the same counts |

This on-air rule is about time, not product: any segment makes the minute on air. It is
separate from `is_airtime_attributed`, which ties an order to its own product's segment and
drives attributed revenue.

## Governance

- Power BI connects to `MARTS` only and does not re-implement these formulas beyond the
  ratios above.
- Base measures are covered by dbt tests (not-null, ranges, relationships to the conformed
  dimensions), plus singular tests for returns and attribution.
- `product`, `presenter`, `channel`, `show`, `date` and `time_minute` are conformed across all
  facts and marts.
