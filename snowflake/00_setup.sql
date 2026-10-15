/* ============================================================================
   Live Commerce — Airtime Intelligence Platform
   Snowflake platform setup: database, schemas, warehouses, RBAC, stage.

   Run as ACCOUNTADMIN (it switches roles where needed). Idempotent: every
   object uses CREATE ... IF NOT EXISTS so re-running is safe.

   Design decisions:
     * Three separated XSMALL warehouses isolate LOAD vs TRANSFORM vs BI compute.
       This is both a performance story (no contention) and a cost-governance
       story (each workload's spend is attributable; AUTO_SUSPEND caps idle cost).
     * Clean schema names (RAW/STAGING/INTERMEDIATE/MARTS/SNAPSHOTS), not dbt's
       default dbt_<layer>. dbt's generate_schema_name macro is overridden to match.
     * Functional RBAC roles (LOADER/TRANSFORMER/BI_READER) under SYSADMIN —
       access is granted by job function, enforced at the warehouse layer. Power BI
       RLS enforces it again at the report layer (defence in depth).
   ============================================================================ */

-- ----------------------------------------------------------------------------
-- 0. Account-level objects (roles + warehouses) — created as the security admin.
-- ----------------------------------------------------------------------------
use role accountadmin;

-- Functional roles -----------------------------------------------------------
create role if not exists role_loader
    comment = 'Loads files from stage into RAW (generator / COPY INTO).';
create role if not exists role_transformer
    comment = 'Runs dbt: reads RAW, writes STAGING/INTERMEDIATE/MARTS/SNAPSHOTS.';
create role if not exists role_bi_reader
    comment = 'Read-only on MARTS for Power BI. Cannot see RAW/STAGING.';

-- Role hierarchy: functional roles roll up to SYSADMIN, not ACCOUNTADMIN.
grant role role_loader      to role sysadmin;
grant role role_transformer to role sysadmin;
grant role role_bi_reader   to role sysadmin;

-- Virtual warehouses — separated by workload, all XSMALL, auto-suspend 60s -----
create warehouse if not exists wh_load
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'COPY INTO / file ingestion compute.';
create warehouse if not exists wh_transform
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'dbt build/run/test compute.';
create warehouse if not exists wh_bi
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'Power BI query compute (Import refresh / DirectQuery).';

-- Warehouse usage grants ------------------------------------------------------
grant usage, operate on warehouse wh_load      to role role_loader;
grant usage, operate on warehouse wh_transform to role role_transformer;
grant usage, operate on warehouse wh_bi        to role role_bi_reader;

-- ----------------------------------------------------------------------------
-- 1. Database + schemas — created as SYSADMIN so it owns them.
-- ----------------------------------------------------------------------------
use role sysadmin;

create database if not exists live_commerce
    comment = 'Live-commerce airtime intelligence — synthetic, governed data product.';

use database live_commerce;

create schema if not exists raw          comment = 'Landing zone. COPY INTO target. Semi-structured lands as VARIANT.';
create schema if not exists staging      comment = '1:1 cleaned/typed/deduped models (dbt views).';
create schema if not exists intermediate comment = 'Business logic — airtime attribution.';
create schema if not exists marts        comment = 'Facts + conformed dims + metrics mart. BI reads ONLY this.';
create schema if not exists snapshots    comment = 'dbt snapshots — SCD2 history (dim_product).';

-- ----------------------------------------------------------------------------
-- 2. Internal stage + file formats for COPY INTO (owned by SYSADMIN, used by loader).
-- ----------------------------------------------------------------------------
create file format if not exists raw.ff_csv
    type = 'csv' field_delimiter = ',' skip_header = 1
    field_optionally_enclosed_by = '"' empty_field_as_null = true
    comment = 'CSV format for generated fact/dim files.';

create file format if not exists raw.ff_json
    type = 'json' strip_outer_array = true
    comment = 'JSON format for semi-structured sources landed as VARIANT.';

create stage if not exists raw.stg_landing
    file_format = raw.ff_csv
    comment = 'Internal stage. Generator PUTs files here; COPY INTO loads RAW.';

-- ----------------------------------------------------------------------------
-- 3. Privilege grants per functional role.
-- ----------------------------------------------------------------------------

-- LOADER: write to RAW + use the stage. Nothing downstream.
grant usage on database live_commerce to role role_loader;
grant usage on schema raw to role role_loader;
grant read, write on stage raw.stg_landing to role role_loader;
grant usage on file format raw.ff_csv  to role role_loader;
grant usage on file format raw.ff_json to role role_loader;
grant create table on schema raw to role role_loader;
grant insert, select on future tables in schema raw to role role_loader;

-- TRANSFORMER (dbt): read RAW, full control of the modelling schemas.
grant usage on database live_commerce to role role_transformer;
grant usage on schema raw to role role_transformer;
grant select on all tables    in schema raw to role role_transformer;
grant select on future tables in schema raw to role role_transformer;
grant usage on schema staging      to role role_transformer;
grant usage on schema intermediate to role role_transformer;
grant usage on schema marts        to role role_transformer;
grant usage on schema snapshots    to role role_transformer;
grant all on schema staging      to role role_transformer;
grant all on schema intermediate to role role_transformer;
grant all on schema marts        to role role_transformer;
grant all on schema snapshots    to role role_transformer;
-- Let the transformer create its own schemas (e.g. CI builds into STAGING_CI/MARTS_CI
-- via DBT_SCHEMA_SUFFIX, isolating automated runs from interactive dev). Future-grants
-- propagate read access so freshly-created CI schemas/objects are usable immediately.
grant create schema on database live_commerce to role role_transformer;
grant select on future tables in database live_commerce to role role_transformer;
grant select on future views  in database live_commerce to role role_transformer;

-- BI_READER: read-only on MARTS only. Cannot touch RAW/STAGING/INTERMEDIATE.
grant usage on database live_commerce to role role_bi_reader;
grant usage on schema marts to role role_bi_reader;
grant select on all tables    in schema marts to role role_bi_reader;
grant select on future tables in schema marts to role role_bi_reader;
grant select on all views     in schema marts to role role_bi_reader;
grant select on future views  in schema marts to role role_bi_reader;

-- ----------------------------------------------------------------------------
-- 4. Assign roles to your trial user. Replace <YOUR_USER> before running,
--    or comment these out and grant in the UI.
-- ----------------------------------------------------------------------------
-- set my_user = '<YOUR_USER>';
-- grant role role_loader      to user identifier($my_user);
-- grant role role_transformer to user identifier($my_user);
-- grant role role_bi_reader   to user identifier($my_user);

-- ----------------------------------------------------------------------------
-- 5. (Optional) Resource Monitor to cap trial spend.
--    Uncomment to enforce a hard credit cap on the trial account.
-- ----------------------------------------------------------------------------
-- use role accountadmin;
-- create resource monitor if not exists rm_trial_cap
--     with credit_quota = 50
--     frequency = monthly
--     start_timestamp = immediately
--     triggers
--         on 80 percent do notify
--         on 100 percent do suspend
--         on 110 percent do suspend_immediate;
-- alter warehouse wh_load      set resource_monitor = rm_trial_cap;
-- alter warehouse wh_transform set resource_monitor = rm_trial_cap;
-- alter warehouse wh_bi        set resource_monitor = rm_trial_cap;
