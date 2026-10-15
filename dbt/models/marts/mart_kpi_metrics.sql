{#
    mart_kpi_metrics — the single governed KPI surface.

    THE "one definition, everywhere" mechanism. Every core monetary/volume KPI is
    defined ONCE here, so Power BI (and any other consumer) reads a single, tested,
    documented definition rather than re-deriving logic in DAX. This is what guarantees
    two reports can never disagree on "revenue".

    GRAIN: one row per (event_date, product_key, presenter_key, channel_key,
    attributed_segment_id). The most granular conformed grain that still lets every KPI
    in KPI_CATALOG.md roll up to any dimension by a simple SUM.

    Base (additive) measures materialised here — see KPI_CATALOG.md for full definitions:
      * gmv                        = Σ gross_amount
      * discounts                  = Σ discount_amount
      * returns_refund             = Σ refund_amount (aligned to the sale)
      * net_revenue_after_returns  = gmv − discounts − returns_refund
      * units_sold                 = Σ quantity
      * returned_units             = Σ returned_qty
      * distinct_orders            = COUNT(DISTINCT order_id)
      * airtime_attributed_revenue = Σ net_amount of airtime-attributed lines only
    Ratios (AOV, return rate, attributed share) are derived from these additive base
    measures in the BI layer — never stored pre-divided at a grain that breaks roll-ups.
#}

with order_lines as (
    select
        order_line_id,
        order_id,
        event_date,
        product_key,
        channel_key,
        attributed_segment_id,
        quantity,
        gross_amount,
        discount_amount,
        net_amount,
        is_airtime_attributed
    from {{ ref('fact_order_line') }}
),

-- The presenter behind an order = the presenter of its attributed segment.
-- Organic orders (segment -1) get the unknown presenter key.
segment_presenter as (
    select
        segment_id,
        presenter_key
    from {{ ref('fact_airtime_segment') }}
),

unknown_presenter as (
    select presenter_key from {{ ref('dim_presenter') }} where presenter_id = -1
),

-- Returns rolled to the order line, so they align to the sale's conformed keys.
returns_per_line as (
    select
        order_line_id,
        sum(returned_qty) as returned_units,
        sum(refund_amount) as returns_refund
    from {{ ref('fact_returns') }}
    group by order_line_id
),

joined as (
    select
        ol.event_date,
        ol.product_key,
        coalesce(sp.presenter_key, up.presenter_key) as presenter_key,
        ol.channel_key,
        ol.attributed_segment_id,
        ol.order_id,
        ol.quantity,
        ol.gross_amount,
        ol.discount_amount,
        ol.net_amount,
        ol.is_airtime_attributed,
        coalesce(r.returned_units, 0) as returned_units,
        coalesce(r.returns_refund, 0) as returns_refund
    from order_lines as ol
    left join segment_presenter as sp on ol.attributed_segment_id = sp.segment_id
    cross join unknown_presenter as up
    left join returns_per_line as r on ol.order_line_id = r.order_line_id
)

select
    event_date,
    product_key,
    presenter_key,
    channel_key,
    attributed_segment_id,
    -- Additive base measures (one definition each).
    sum(gross_amount) as gmv,
    sum(discount_amount) as discounts,
    sum(returns_refund) as returns_refund,
    sum(gross_amount) - sum(discount_amount) - sum(returns_refund) as net_revenue_after_returns,
    sum(quantity) as units_sold,
    sum(returned_units) as returned_units,
    count(distinct order_id) as distinct_orders,
    sum(case when is_airtime_attributed then net_amount else 0 end) as airtime_attributed_revenue,
    sum(net_amount) as total_net_revenue
from joined
group by event_date, product_key, presenter_key, channel_key, attributed_segment_id
