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
