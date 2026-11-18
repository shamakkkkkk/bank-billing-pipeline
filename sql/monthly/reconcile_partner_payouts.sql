-- Check 2: no partner statement of the month pays out more than the revenue
-- SaveHub earned from the customers that partner referred.
--
-- Contract: one row per problem (EMPTY result = reconciled), columns
-- check_name, subject, expected_eur, actual_eur. No parameters.
--
-- The limit comes from daily_revenue (the source of truth), NOT from
-- partner_statements.referred_revenue_eur: that column was written by the same
-- query as the amount, so a bug there would hide itself.
WITH referred_revenue AS (
    SELECT
        d.partner_id,
        SUM(d.revenue_eur) AS revenue_eur  -- exact: compare before any rounding
    FROM daily_revenue AS d
    JOIN billing_period AS bp
        ON d.revenue_date BETWEEN bp.period_start AND bp.period_end
    WHERE d.partner_id IS NOT NULL
    GROUP BY d.partner_id
)

SELECT
    CASE
        WHEN r.partner_id IS NULL THEN 'statement_without_referred_revenue'
        ELSE 'payout_exceeds_revenue'
    END                          AS check_name,
    s.partner_id                 AS subject,
    ROUND(r.revenue_eur, 2)      AS expected_eur,  -- the ceiling, shown in cents
    s.amount_eur                 AS actual_eur
FROM partner_statements AS s
JOIN billing_period AS bp
    ON s.statement_month = bp.period_start
-- LEFT JOIN: a statement for a partner with no referred revenue at all must be
-- reported, not dropped (with an inner join it would vanish from the check).
LEFT JOIN referred_revenue AS r
    ON r.partner_id = s.partner_id
WHERE r.partner_id IS NULL
   OR s.amount_eur > r.revenue_eur
