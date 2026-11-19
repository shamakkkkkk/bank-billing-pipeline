-- Daily platform revenue per customer for one date (parameter: the date).
--
--   revenue = balance_eur * daily_fee_rate      (daily_fee_rate = annual / 365)
--
-- Which rate applies? The bank_rates row with the latest valid_from on or
-- before the date. LEAD() turns the list of start dates into [valid_from,
-- valid_to) intervals, so a rate change mid-month splits cleanly by day.
-- Not rounded here: rounding to cents happens once, on the monthly invoice.
INSERT INTO daily_revenue (
    revenue_date, bank, customer_id, partner_id,
    balance_eur, annual_fee_rate, daily_fee_rate, revenue_eur
)
SELECT
    b.balance_date,
    b.bank,
    b.customer_id,
    b.partner_id,
    b.balance_eur,
    r.annual_fee_rate,
    r.daily_fee_rate,
    -- Exact product needs 2 + 12 = 14 decimals. Snowflake caps the scale of a
    -- product at max(scale1, scale2, 12) and would silently round to 12; giving
    -- the rate scale 14 lifts that cap. DuckDB is exact either way.
    b.balance_eur * CAST(r.daily_fee_rate AS NUMERIC(20, 14)) AS revenue_eur
FROM balances AS b
JOIN (
    SELECT
        bank,
        annual_fee_rate,
        daily_fee_rate,
        valid_from,
        LEAD(valid_from) OVER (PARTITION BY bank ORDER BY valid_from) AS valid_to
    FROM bank_rates
) AS r
    ON  r.bank = b.bank
    AND b.balance_date >= r.valid_from
    AND (r.valid_to IS NULL OR b.balance_date < r.valid_to)
WHERE b.balance_date = ?
