{#
    Additive KPI base measures (definitions in docs/KPI_CATALOG.md), so BI sums them instead
    of re-deriving logic. Ratios such as AOV and return rate are computed in BI.
    Grain: event_date x product_key x presenter_key x channel_key x attributed_segment_id.
    Returns are aligned to the date and keys of the original sale.
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

-- An order's presenter is its attributed segment's presenter; organic orders get -1.
segment_presenter as (
    select
        segment_id,
        presenter_key
    from {{ ref('fact_airtime_segment') }}
),

unknown_presenter as (
    select presenter_key from {{ ref('dim_presenter') }}
    where presenter_id = -1
),

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
