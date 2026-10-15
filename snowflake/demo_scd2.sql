/* ============================================================================
   SCD2 demonstration — prove the product snapshot captures slowly-changing history.

   Goal: show that when a product's tracked attribute (category / price band / price)
   changes, the dbt snapshot closes the old version (sets dbt_valid_to) and inserts a
   new current version (dbt_valid_to = NULL) — point-in-time history, automatically.

   PREREQUISITE: the pipeline has already been built at least once
   (dbt build, so dim_product_snapshot exists with a baseline version per product).

   STEP 1 — baseline: how many versions does product 1 have right now?
   Run this first and note the result (expect 1 row).
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
   STEP 2 — simulate a source change: reclassify product 1 and bump its price.
   This edits the RAW landing table the way a real upstream source change would.
   (Run as the loader role, which owns writes to RAW.)
   ---------------------------------------------------------------------------- */
use role role_loader;

update raw.dim_product
set category = 'Jewellery',
    list_price = 249.99,
    list_price_band = 'premium'
where product_id = 1
  and is_current = true;

/* ----------------------------------------------------------------------------
   STEP 3 — re-run the pipeline so staging refreshes and the snapshot re-evaluates:

       (in your terminal)
       cd dbt
       dbt run  --select stg_products
       dbt snapshot

   dbt compares the new current row against the stored version, sees category/price/
   band changed, and writes a NEW version row (closing the old one).

   STEP 4 — verify: product 1 now has TWO versions.
   Re-run the STEP 1 query. Expected:
     * old row: category=<original>, dbt_valid_to = <timestamp>  (expired)
     * new row: category=Jewellery, list_price=249.99, dbt_valid_to = NULL (CURRENT)

   This is SCD Type 2 working: the sale that aired before the change still joins to the
   old version (its category/price at the time), while new sales join to the new one.
   ---------------------------------------------------------------------------- */

/* ----------------------------------------------------------------------------
   RESET (optional) — restore product 1 so the demo is repeatable. After running this,
   re-run `dbt run --select stg_products && dbt snapshot` to close the Jewellery version
   and open a third version back at the original values.
   NOTE: replace the values below with product 1's ORIGINAL values from STEP 1.
   ---------------------------------------------------------------------------- */
-- use role role_loader;
-- update raw.dim_product
-- set category = '<ORIGINAL_CATEGORY>',
--     list_price = <ORIGINAL_PRICE>,
--     list_price_band = '<ORIGINAL_BAND>'
-- where product_id = 1 and is_current = true;
