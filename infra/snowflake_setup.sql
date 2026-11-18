-- One-time Snowflake setup, run by an account admin in a Snowsight worksheet.
-- Idempotent: safe to run again. Replace __PUBLIC_KEY__ with the contents of
-- the public key (without the BEGIN/END lines); scripts/snowflake_setup.sh does
-- that for you and prints the ready-to-paste script.
--
-- What it creates, and why:
--   SAVEHUB_WH        smallest warehouse, suspends after 60 s idle: pay only while running
--   SAVEHUB_MONITOR   hard cap on credits: the warehouse is suspended at the quota
--   SAVEHUB.BILLING   the pipeline's schema
--   SAVEHUB_PIPELINE  role + service user with key-pair auth (no password), owns
--                     only this schema: least privilege for the pipeline

USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS SAVEHUB_WH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE;

CREATE RESOURCE MONITOR IF NOT EXISTS SAVEHUB_MONITOR
    WITH CREDIT_QUOTA = 10
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS ON 80 PERCENT DO NOTIFY
             ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE SAVEHUB_WH SET RESOURCE_MONITOR = SAVEHUB_MONITOR;

CREATE DATABASE IF NOT EXISTS SAVEHUB;
CREATE SCHEMA IF NOT EXISTS SAVEHUB.BILLING;

CREATE ROLE IF NOT EXISTS SAVEHUB_PIPELINE;
GRANT USAGE ON WAREHOUSE SAVEHUB_WH TO ROLE SAVEHUB_PIPELINE;
GRANT USAGE ON DATABASE SAVEHUB TO ROLE SAVEHUB_PIPELINE;
-- Own the pipeline schema (create tables, temp tables), plus create throwaway
-- schemas for the integration test, which drops them again.
GRANT OWNERSHIP ON SCHEMA SAVEHUB.BILLING TO ROLE SAVEHUB_PIPELINE COPY CURRENT GRANTS;
GRANT CREATE SCHEMA ON DATABASE SAVEHUB TO ROLE SAVEHUB_PIPELINE;
-- Let human admins see the pipeline's data in Snowsight.
GRANT ROLE SAVEHUB_PIPELINE TO ROLE SYSADMIN;

CREATE USER IF NOT EXISTS SAVEHUB_PIPELINE
    TYPE = SERVICE
    DEFAULT_ROLE = SAVEHUB_PIPELINE
    DEFAULT_WAREHOUSE = SAVEHUB_WH
    DEFAULT_NAMESPACE = SAVEHUB.BILLING
    COMMENT = 'Airflow billing pipeline (key-pair auth)';
ALTER USER SAVEHUB_PIPELINE SET RSA_PUBLIC_KEY = '__PUBLIC_KEY__';
GRANT ROLE SAVEHUB_PIPELINE TO USER SAVEHUB_PIPELINE;

-- Check: the last line shows the key fingerprint registered for the user.
DESC USER SAVEHUB_PIPELINE;
