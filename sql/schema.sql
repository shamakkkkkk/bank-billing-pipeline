-- Warehouse schema. Idempotent: safe to run before every DAG run.
-- Money: NUMERIC, never FLOAT. Balances have cents (2 decimals); revenue keeps
-- full precision and is rounded to cents exactly once, on monthly documents.

-- Reference data, refreshed from seeds/*.csv on every run.
CREATE TABLE IF NOT EXISTS bank_rates (
    bank            VARCHAR       NOT NULL,
    annual_fee_rate NUMERIC(9, 6) NOT NULL,  -- 0.005 = 0.5 % per year
    -- annual_fee_rate / 365 at 12 decimals, computed in Python (exact Decimal):
    -- DuckDB would compute DECIMAL / 365 as DOUBLE.
    daily_fee_rate  NUMERIC(18, 12) NOT NULL,
    valid_from      DATE          NOT NULL,
    PRIMARY KEY (bank, valid_from)
);

CREATE TABLE IF NOT EXISTS partners (
    partner_id    VARCHAR       NOT NULL PRIMARY KEY,
    name          VARCHAR       NOT NULL,
    revenue_share NUMERIC(5, 4) NOT NULL  -- 0.20 = 20 % of revenue from referred customers
);

-- Raw daily balances, one row per customer per day, as sent by the bank.
CREATE TABLE IF NOT EXISTS balances (
    balance_date DATE           NOT NULL,
    bank         VARCHAR        NOT NULL,
    customer_id  VARCHAR        NOT NULL,
    partner_id   VARCHAR,                  -- NULL: customer came without a partner
    balance_eur  NUMERIC(18, 2) NOT NULL,
    source_file  VARCHAR        NOT NULL,  -- lineage: which S3 object this row came from
    loaded_at    TIMESTAMP      NOT NULL,
    PRIMARY KEY (balance_date, bank, customer_id)
);

-- Platform revenue per customer per day = balance * daily_fee_rate. Not rounded.
CREATE TABLE IF NOT EXISTS daily_revenue (
    revenue_date    DATE            NOT NULL,
    bank            VARCHAR         NOT NULL,
    customer_id     VARCHAR         NOT NULL,
    partner_id      VARCHAR,
    balance_eur     NUMERIC(18, 2)  NOT NULL,
    annual_fee_rate NUMERIC(9, 6)   NOT NULL,  -- copied: the row explains itself
    daily_fee_rate  NUMERIC(18, 12) NOT NULL,
    revenue_eur     NUMERIC(38, 14) NOT NULL,  -- exact product of the two above
    PRIMARY KEY (revenue_date, bank, customer_id)
);

-- Partner share of revenue from customers they referred, per customer per day.
-- Not rounded (same rule as daily_revenue). Customers without a partner: no row.
CREATE TABLE IF NOT EXISTS partner_payouts_daily (
    payout_date   DATE            NOT NULL,
    partner_id    VARCHAR         NOT NULL,
    bank          VARCHAR         NOT NULL,
    customer_id   VARCHAR         NOT NULL,
    revenue_eur   NUMERIC(38, 14) NOT NULL,  -- copied from daily_revenue
    revenue_share NUMERIC(5, 4)   NOT NULL,  -- copied from partners
    payout_eur    NUMERIC(38, 18) NOT NULL,  -- exact product of the two above
    PRIMARY KEY (payout_date, partner_id, bank, customer_id)
);

-- Monthly documents. THE place money is rounded to cents: ROUND(SUM(...), 2)
-- in sql/monthly/create_*.sql (half away from zero, same in DuckDB and Snowflake).
-- The exact unrounded sums are kept next to the rounded amounts for audit.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id        VARCHAR         NOT NULL PRIMARY KEY,  -- INV-2026-10-ALPHA: same id on rerun
    invoice_month     DATE            NOT NULL,              -- first day of the month
    bank              VARCHAR         NOT NULL,
    period_start      DATE            NOT NULL,
    period_end        DATE            NOT NULL,
    customer_days     INTEGER         NOT NULL,              -- daily_revenue rows billed
    revenue_eur_exact NUMERIC(38, 14) NOT NULL,
    amount_eur        NUMERIC(18, 2)  NOT NULL,              -- what the bank pays
    currency          VARCHAR         NOT NULL,
    created_at        TIMESTAMP       NOT NULL
);

CREATE TABLE IF NOT EXISTS partner_statements (
    statement_id         VARCHAR         NOT NULL PRIMARY KEY,  -- STM-2026-10-P01
    statement_month      DATE            NOT NULL,
    partner_id           VARCHAR         NOT NULL,
    partner_name         VARCHAR         NOT NULL,
    period_start         DATE            NOT NULL,
    period_end           DATE            NOT NULL,
    referred_customers   INTEGER         NOT NULL,
    referred_revenue_eur NUMERIC(18, 2)  NOT NULL,  -- revenue from its customers, for context
    payout_eur_exact     NUMERIC(38, 18) NOT NULL,
    amount_eur           NUMERIC(18, 2)  NOT NULL,  -- what we pay the partner
    currency             VARCHAR         NOT NULL,
    created_at           TIMESTAMP       NOT NULL
);
