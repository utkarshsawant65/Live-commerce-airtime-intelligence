{#
    Singular test (data-quality robustness): any row the VARIANT parser marked is_valid
    MUST have complete, well-typed required fields. If a row is flagged valid but has a
    null order_line_id / product_id / quantity / event_ts, the validation logic is broken
    and dirty data would leak downstream.

    This is the guardrail that proves the schema-on-read + quarantine design actually
    holds: clean-marked rows are genuinely clean; everything else is in quarantine.

    Returns offending rows; passes when zero rows.
#}

select
    order_line_id,
    product_id,
    quantity,
    event_ts_utc
from {{ ref('stg_order_lines_variant') }}
where is_valid = true
    and (
        order_line_id is null
        or product_id is null
        or quantity is null
        or quantity < 1
        or event_ts_utc is null
        or net_amount is null
        or net_amount < 0
    )
