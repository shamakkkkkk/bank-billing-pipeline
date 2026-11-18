-- One statement per partner for the month in billing_period.
-- Rounded once, like invoices: ROUND(SUM(payout_eur), 2).
INSERT INTO partner_statements (
    statement_id, statement_month, partner_id, partner_name, period_start, period_end,
    referred_customers, referred_revenue_eur, payout_eur_exact, amount_eur, currency, created_at
)
SELECT
    'STM-' || bp.month_label || '-' || p.partner_id AS statement_id,
    bp.period_start                                 AS statement_month,
    p.partner_id,
    pa.name                                         AS partner_name,
    bp.period_start,
    bp.period_end,
    COUNT(DISTINCT p.customer_id)                   AS referred_customers,
    ROUND(SUM(p.revenue_eur), 2)                    AS referred_revenue_eur,
    SUM(p.payout_eur)                               AS payout_eur_exact,
    ROUND(SUM(p.payout_eur), 2)                     AS amount_eur,
    'EUR'                                           AS currency,
    bp.created_at
FROM partner_payouts_daily AS p
JOIN partners AS pa
    ON pa.partner_id = p.partner_id
JOIN billing_period AS bp
    ON p.payout_date BETWEEN bp.period_start AND bp.period_end
GROUP BY p.partner_id, pa.name, bp.month_label, bp.period_start, bp.period_end, bp.created_at
