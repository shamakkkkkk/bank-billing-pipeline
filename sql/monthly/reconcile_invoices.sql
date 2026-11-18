-- Check 1: the invoices of the month in billing_period match daily_revenue, to the cent.
--
-- Contract: one row per problem (EMPTY result = reconciled), columns
-- check_name, subject, expected_eur, actual_eur. No parameters.
--
-- "Expected" is recomputed from the source of truth (daily_revenue) with the
-- same single rounding the invoice uses: ROUND(SUM(exact), 2). Comparing with
-- a sum of rounded daily amounts would flag correct invoices; comparing with
-- the unrounded sum would flag every invoice by a fraction of a cent.
WITH expected AS (
    SELECT
        d.bank,
        ROUND(SUM(d.revenue_eur), 2) AS amount_eur
    FROM daily_revenue AS d
    JOIN billing_period AS bp
        ON d.revenue_date BETWEEN bp.period_start AND bp.period_end
    GROUP BY d.bank
),

invoiced AS (
    SELECT
        i.bank,
        i.amount_eur
    FROM invoices AS i
    JOIN billing_period AS bp
        ON i.invoice_month = bp.period_start
)

-- FULL OUTER JOIN finds problems on both sides: revenue without an invoice
-- (invoiced.bank IS NULL) and an invoice without revenue (expected.bank IS NULL).
SELECT
    CASE
        WHEN i.bank IS NULL THEN 'invoice_missing'
        WHEN e.bank IS NULL THEN 'invoice_without_revenue'
        ELSE 'invoice_amount_mismatch'
    END                         AS check_name,
    COALESCE(e.bank, i.bank)    AS subject,
    e.amount_eur                AS expected_eur,
    i.amount_eur                AS actual_eur
FROM expected AS e
FULL OUTER JOIN invoiced AS i
    ON i.bank = e.bank
-- Spell out the NULL cases: "e.amount_eur <> NULL" is NULL, not TRUE, so a
-- plain inequality would silently let a missing invoice through.
WHERE i.bank IS NULL
   OR e.bank IS NULL
   OR e.amount_eur <> i.amount_eur
