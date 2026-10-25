/* ============================================================================
   Snapshot walkthrough: change a tracked product attribute in place and watch the
   capture-time audit trail (dim_product_snapshot) close the old version and open a
   new one. dim_product itself is dated by the source's valid_from / valid_to.

   Prerequisite: `dbt build` has run at least once, so dim_product_snapshot exists.

   STEP 1: current versions of product 1 (expect one row on a fresh build).
   ---------------------------------------------------------------------------- */
use role role_transformer;
use database live_commerce;

select
    product_id,
    category,
    list_price_band,
    list_price,
    dbt_valid_from,
    dbt_valid_to,
    iff(dbt_valid_to is null, 'CURRENT', 'expired') as version_state
from snapshots.dim_product_snapshot
where product_id = 1
order by dbt_valid_from;

/* ----------------------------------------------------------------------------
   STEP 2: simulate a source change. Reclassify product 1 and change its price
   in RAW (as the loader role, which owns the RAW tables).
   ---------------------------------------------------------------------------- */
use role role_loader;

update raw.dim_product
set category = 'Jewellery',
    list_price = 249.99,
    list_price_band = 'premium'
where product_id = 1
  and is_current = true;

/* ----------------------------------------------------------------------------
   STEP 3: from the dbt/ folder, re-run staging and the snapshot:

       dbt run --select stg_products
       dbt snapshot

   STEP 4: re-run the STEP 1 query. Expected:
     * old row: original values, dbt_valid_to = time of this snapshot run
     * new row: category = Jewellery, list_price = 249.99, dbt_valid_to = NULL

   The new version is effective from the snapshot run that captured the change, so
   in dim_product orders with event_ts_utc before that run resolve to the old version
   and later orders to the new one.
   ---------------------------------------------------------------------------- */

/* ----------------------------------------------------------------------------
   RESET (optional): restore product 1 using its original values from STEP 1, then
   re-run `dbt run --select stg_products && dbt snapshot` (this opens a third version).
   ---------------------------------------------------------------------------- */
-- use role role_loader;
-- update raw.dim_product
-- set category = '<ORIGINAL_CATEGORY>',
--     list_price = <ORIGINAL_PRICE>,
--     list_price_band = '<ORIGINAL_BAND>'
-- where product_id = 1 and is_current = true;
