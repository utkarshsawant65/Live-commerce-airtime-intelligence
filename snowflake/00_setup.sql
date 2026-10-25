/* ============================================================================
   Live Commerce: Snowflake setup (database, schemas, warehouses, roles, stage).

   Run as a user with ACCOUNTADMIN; the script switches roles as it goes.
   Idempotent: every object uses CREATE ... IF NOT EXISTS.
   One XSMALL warehouse per workload (load / transform / BI) so spend is attributable.
   ============================================================================ */

-- ----------------------------------------------------------------------------
-- 0. Roles and warehouses.
-- ----------------------------------------------------------------------------
use role accountadmin;

create role if not exists role_loader
    comment = 'Loads files from stage into RAW (generator / COPY INTO).';
create role if not exists role_transformer
    comment = 'Runs dbt: reads RAW, writes STAGING/INTERMEDIATE/MARTS/SNAPSHOTS.';
create role if not exists role_bi_reader
    comment = 'Read-only on MARTS for Power BI. Cannot see RAW/STAGING.';

-- Functional roles roll up to SYSADMIN.
grant role role_loader      to role sysadmin;
grant role role_transformer to role sysadmin;
grant role role_bi_reader   to role sysadmin;

create warehouse if not exists wh_load
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'COPY INTO / file ingestion compute.';
create warehouse if not exists wh_transform
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'dbt build/run/test compute.';
create warehouse if not exists wh_bi
    warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true
    comment = 'Power BI query compute (Import refresh / DirectQuery).';

grant usage, operate on warehouse wh_load      to role role_loader;
grant usage, operate on warehouse wh_transform to role role_transformer;
grant usage, operate on warehouse wh_bi        to role role_bi_reader;

-- ----------------------------------------------------------------------------
-- 1. Database and schemas, owned by SYSADMIN.
-- ----------------------------------------------------------------------------
use role sysadmin;

create database if not exists live_commerce
    comment = 'Live-commerce airtime analytics (synthetic data).';

use database live_commerce;

create schema if not exists raw          comment = 'Landing zone for COPY INTO. Semi-structured data lands as VARIANT.';
create schema if not exists staging      comment = 'dbt staging views: cleaned, typed, deduplicated.';
create schema if not exists intermediate comment = 'dbt intermediate models.';
create schema if not exists marts        comment = 'Facts, dimensions and marts. BI reads only this schema.';
create schema if not exists snapshots    comment = 'dbt snapshots (dim_product SCD2 history).';

-- ----------------------------------------------------------------------------
-- 2. Internal stage and file formats for COPY INTO.
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
-- 3. Privileges. Future grants need MANAGE GRANTS, so this section runs as
--    SECURITYADMIN. All future grants are schema-level (a schema-level future
--    grant overrides database-level ones for that schema, so mixing levels is avoided).
-- ----------------------------------------------------------------------------
use role securityadmin;

-- LOADER: create and write tables in RAW, use the stage and file formats.
grant usage on database live_commerce to role role_loader;
grant usage on schema live_commerce.raw to role role_loader;
grant read, write on stage live_commerce.raw.stg_landing to role role_loader;
grant usage on file format live_commerce.raw.ff_csv  to role role_loader;
grant usage on file format live_commerce.raw.ff_json to role role_loader;
grant create table on schema live_commerce.raw to role role_loader;
grant insert, select on future tables in schema live_commerce.raw to role role_loader;

-- TRANSFORMER (dbt): read RAW, full control of the modelling schemas. It may also
-- create schemas, so CI can build into its own STAGING_CI / MARTS_CI / ... schemas;
-- it owns everything it creates there, so no extra grants are needed.
grant usage on database live_commerce to role role_transformer;
grant usage on schema live_commerce.raw to role role_transformer;
grant select on all tables    in schema live_commerce.raw to role role_transformer;
grant select on future tables in schema live_commerce.raw to role role_transformer;
grant all on schema live_commerce.staging      to role role_transformer;
grant all on schema live_commerce.intermediate to role role_transformer;
grant all on schema live_commerce.marts        to role role_transformer;
grant all on schema live_commerce.snapshots    to role role_transformer;
grant create schema on database live_commerce to role role_transformer;

-- BI_READER: read-only on MARTS.
grant usage on database live_commerce to role role_bi_reader;
grant usage on schema live_commerce.marts to role role_bi_reader;
grant select on all tables    in schema live_commerce.marts to role role_bi_reader;
grant select on future tables in schema live_commerce.marts to role role_bi_reader;
grant select on all views     in schema live_commerce.marts to role role_bi_reader;
grant select on future views  in schema live_commerce.marts to role role_bi_reader;

-- ----------------------------------------------------------------------------
-- 4. Grant the three roles to the user running this script.
-- ----------------------------------------------------------------------------
set my_user = current_user();
grant role role_loader      to user identifier($my_user);
grant role role_transformer to user identifier($my_user);
grant role role_bi_reader   to user identifier($my_user);

-- The CI job both loads RAW and runs dbt, so its user needs both roles.
-- set ci_user = '<CI_USER>';
-- grant role role_loader      to user identifier($ci_user);
-- grant role role_transformer to user identifier($ci_user);

-- ----------------------------------------------------------------------------
-- 5. (Optional) Resource monitor to cap spend.
-- ----------------------------------------------------------------------------
-- use role accountadmin;
-- create resource monitor if not exists rm_credit_cap
--     with credit_quota = 50
--     frequency = monthly
--     start_timestamp = immediately
--     triggers
--         on 80 percent do notify
--         on 100 percent do suspend
--         on 110 percent do suspend_immediate;
-- alter warehouse wh_load      set resource_monitor = rm_credit_cap;
-- alter warehouse wh_transform set resource_monitor = rm_credit_cap;
-- alter warehouse wh_bi        set resource_monitor = rm_credit_cap;
