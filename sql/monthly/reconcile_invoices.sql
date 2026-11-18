-- TODO: write this query. The task is described in docs/reconciliation_task.md.
--
-- Check 1: the invoices of the month in billing_period match daily_revenue
-- for that month, to the cent.
--
-- Contract:
--   * one row per problem found; an EMPTY result means "reconciled"
--   * columns: check_name, subject, expected_eur, actual_eur
--     (subject = what is wrong, e.g. the bank; expected/actual may be NULL)
--   * no parameters: join billing_period (month_label, period_start, period_end)
--
-- While this file still contains the word TODO, the reconcile task refuses to run.
SELECT
    CAST(NULL AS VARCHAR)        AS check_name,
    CAST(NULL AS VARCHAR)        AS subject,
    CAST(NULL AS NUMERIC(18, 2)) AS expected_eur,
    CAST(NULL AS NUMERIC(18, 2)) AS actual_eur
WHERE 1 = 0
