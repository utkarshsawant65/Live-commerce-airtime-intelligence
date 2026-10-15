{#
    stg_order_lines_quarantine — the rows that failed validation in the VARIANT feed.

    Bad rows are CAPTURED here (not dropped, not allowed to break the load) with the
    reason they failed and their original payload context, so they can be inspected,
    reported on, and backfilled once the source is fixed. A non-empty quarantine is an
    observable data-quality signal, not a silent loss.

    Deduped on order_line_id where present; rows with a null id keep all occurrences
    (we cannot key them) so nothing is lost.
#}

select
    order_line_id,
    order_id,
    product_id,
    channel_id,
    event_ts_utc,
    quantity,
    source_file,
    loaded_at_utc,
    dq_failure_reason
from {{ ref('stg_order_lines_variant') }}
where is_valid = false
