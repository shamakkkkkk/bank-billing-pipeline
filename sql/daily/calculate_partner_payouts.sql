-- Partner payouts for one date (parameter: the date).
--
--   payout = revenue_eur * revenue_share     (for customers a partner referred)
--
-- Inner join on purpose: customers without a partner (partner_id NULL) get no
-- payout row. A partner_id that is NOT in the partners table would also be
-- dropped by the join: daily.calculate_partner_payouts() checks the row counts
-- and fails instead of silently paying nobody.
INSERT INTO partner_payouts_daily (
    payout_date, partner_id, bank, customer_id,
    revenue_eur, revenue_share, payout_eur
)
SELECT
    d.revenue_date,
    d.partner_id,
    d.bank,
    d.customer_id,
    d.revenue_eur,
    p.revenue_share,
    d.revenue_eur * p.revenue_share AS payout_eur
FROM daily_revenue AS d
JOIN partners AS p
    ON p.partner_id = d.partner_id
WHERE d.revenue_date = ?
