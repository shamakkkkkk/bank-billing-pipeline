-- One invoice per bank for the month in billing_period (a one-row temp table
-- created by savehub.monthly: month_label, period_start, period_end, created_at).
--
-- The single rounding step of the whole pipeline: ROUND(SUM(revenue_eur), 2).
-- Summing exact values first and rounding once avoids the drift you get from
-- rounding ~3,100 tiny daily amounts one by one.
INSERT INTO invoices (
    invoice_id, invoice_month, bank, period_start, period_end,
    customer_days, revenue_eur_exact, amount_eur, currency, created_at
)
SELECT
    'INV-' || bp.month_label || '-' || UPPER(d.bank) AS invoice_id,
    bp.period_start                                  AS invoice_month,
    d.bank,
    bp.period_start,
    bp.period_end,
    COUNT(*)                                         AS customer_days,
    SUM(d.revenue_eur)                               AS revenue_eur_exact,
    ROUND(SUM(d.revenue_eur), 2)                     AS amount_eur,
    'EUR'                                            AS currency,
    bp.created_at
FROM daily_revenue AS d
JOIN billing_period AS bp
    ON d.revenue_date BETWEEN bp.period_start AND bp.period_end
GROUP BY d.bank, bp.month_label, bp.period_start, bp.period_end, bp.created_at
