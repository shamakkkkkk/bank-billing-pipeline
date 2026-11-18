-- TODO: write this query. The task is described in docs/reconciliation_task.md.
--
-- Check 2: no partner statement of the month in billing_period pays out more
-- than the revenue SaveHub earned from that partner's referred customers.
--
-- Contract: same as reconcile_invoices.sql
--   * one row per problem; EMPTY result = reconciled
--   * columns: check_name, subject, expected_eur, actual_eur
--   * no parameters: join billing_period
--
-- While this file still contains the word TODO, the reconcile task refuses to run.
SELECT
    CAST(NULL AS VARCHAR)        AS check_name,
    CAST(NULL AS VARCHAR)        AS subject,
    CAST(NULL AS NUMERIC(18, 2)) AS expected_eur,
    CAST(NULL AS NUMERIC(18, 2)) AS actual_eur
WHERE 1 = 0
