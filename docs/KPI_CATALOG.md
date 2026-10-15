# KPI Catalog

The single source of truth for every KPI in this project. Each KPI is defined **once**
in the dbt marts layer (primarily [`mart_kpi_metrics`](../dbt/models/marts/mart_kpi_metrics.sql),
[`mart_segment_performance`](../dbt/models/marts/mart_segment_performance.sql), and
[`mart_conversion_windows`](../dbt/models/marts/mart_conversion_windows.sql)) and consumed by
Power BI without re-derivation. This is the mechanism that stops two reports from
disagreeing on "revenue": one definition, tested and documented, BI reads marts only.

**Conventions**
- All monetary values are in the shop's reporting currency; all timestamps are UTC,
  converted only at the BI layer.
- *Additive* base measures (sums, counts) are stored at the mart grain and roll up to any
  dimension by `SUM`. *Ratio* KPIs are computed from those base measures at query time
  (a Power BI measure), never stored pre-divided at a grain that would break roll-ups.
- ⭐ marks the hero KPIs that make this a live-commerce product.

---

## Base measures — `mart_kpi_metrics`

Grain: one row per (`event_date`, `product_key`, `presenter_key`, `channel_key`,
`attributed_segment_id`). Every KPI below rolls up to any of those dimensions.

| KPI | Formula | Type | Reported grain |
|---|---|---|---|
| **GMV** | `Σ gross_amount` | additive | any dimension |
| **Discounts** | `Σ discount_amount` | additive | any dimension |
| **Returns Refund** | `Σ refund_amount` (returns aligned to the sale) | additive | any dimension |
| **Net Revenue after Returns** | `GMV − Discounts − Returns Refund` | additive | show / presenter / channel / category |
| **Units Sold** | `Σ quantity` | additive | any dimension |
| **Returned Units** | `Σ returned_qty` | additive | product / presenter / category / channel |
| **Distinct Orders** | `COUNT(DISTINCT order_id)` | semi-additive | channel / show |
| **Airtime-Attributed Revenue** | `Σ net_amount WHERE is_airtime_attributed` | additive | segment / presenter |

### Ratio KPIs derived from the base measures

| KPI | Formula | Reported grain | Notes / filters |
|---|---|---|---|
| **AOV** (avg order value) | `Net Revenue after Returns ÷ Distinct Orders` | channel / show | guard `Distinct Orders > 0` |
| **Return Rate** | `Returned Units ÷ Units Sold` | product / presenter / category / channel | always in `[0, 1]` (enforced by a singular test) |
| **Airtime-Attributed Revenue Share** | `Airtime-Attributed Revenue ÷ Net Revenue` | segment / presenter | share of revenue the airtime drove |

---

## Hero KPIs

### ⭐ Revenue per Broadcast Minute — `mart_segment_performance`

Grain: one row per **airtime segment**.

| | |
|---|---|
| **Formula** | `attributed_revenue ÷ segment_duration_minutes` |
| **Reported grain** | segment / presenter / product / show |
| **Filters** | airtime-attributed revenue only; guard `segment_duration_minutes > 0` |
| **Why it matters** | The core efficiency metric of live commerce: which product × presenter × time window earns the most per minute of air. |

### ⭐ Conversion Rate & Conversion Lift — `mart_conversion_windows`

Grain: one row per **airtime_state** (`on_air` / `off_air`).

| | |
|---|---|
| **Conversion Rate** | `orders ÷ web_sessions` (per state; online channels only) |
| **Conversion Lift (absolute)** | `conversion_rate(on_air) − conversion_rate(off_air)` |
| **Conversion Lift (relative)** | `conversion_rate(on_air) ÷ conversion_rate(off_air) − 1` |
| **Reported grain** | population (on-air vs off-air) |
| **Filters** | online channels with `web_sessions > 0` (broadcast has viewers, not sessions) |
| **Statistical backing** | A two-proportion z-test ([`analysis/conversion_lift_test.py`](../analysis/conversion_lift_test.py)) tests whether the on-air lift is significant. The dashboard and the test read the same counts. |

---

## Governance notes

- **One tool reads these:** Power BI connects to `MARTS` only. No DAX re-implements a KPI
  formula that belongs in dbt.
- **Tested:** every base measure has dbt tests (not-null, non-negative ranges, referential
  integrity to the conformed dimensions); business invariants (return rate ≤ 1, no future
  attribution) are enforced by singular tests.
- **Conformed dimensions:** `product`, `presenter`, `channel`, `show`, `date`, `time_minute`
  are shared across all marts, so a slice means the same thing in every report.
