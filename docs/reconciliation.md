# Reconciliation

The `reconcile` task of `monthly_invoices` is the last gate before money leaves
the company. It runs two SQL checks; if either returns a row, the task fails and
nothing is exported.

| Check | File | Question it answers |
|---|---|---|
| Invoices | [`reconcile_invoices.sql`](../sql/monthly/reconcile_invoices.sql) | Does every bank's invoice equal what `daily_revenue` says it owes for the month, to the cent? |
| Partner payouts | [`reconcile_partner_payouts.sql`](../sql/monthly/reconcile_partner_payouts.sql) | Does any partner get more than the revenue from the customers it referred? |

## What each check catches

**Invoices**

| Problem | Reported as |
|---|---|
| Amount differs, even by 0.01 | `invoice_amount_mismatch` |
| A bank earned revenue but has no invoice | `invoice_missing` |
| An invoice exists for a bank with no revenue | `invoice_without_revenue` |
| Daily data changed after the invoice was created (a day re-run with a corrected file) | `invoice_amount_mismatch` |

**Partner payouts**

| Problem | Reported as |
|---|---|
| Statement amount above the revenue from the partner's customers (e.g. a share typed as `2.0` instead of `0.20`) | `payout_exceeds_revenue` |
| Statement for a partner with no referred revenue at all | `statement_without_referred_revenue` |

## Design choices

**Recompute from the source of truth.** Both checks start from `daily_revenue`,
never from columns that the document queries wrote themselves
(`partner_statements.referred_revenue_eur`, for example). A bug in the document
query would otherwise produce matching wrong numbers on both sides.

**Compare like with like.** An invoice is `ROUND(SUM(exact revenue), 2)`. The
check recomputes exactly that. Comparing with the unrounded sum would flag every
invoice by a fraction of a cent; comparing with a sum of rounded daily amounts
would flag correct invoices because of accumulated rounding.

**Look for gaps on both sides.** A `FULL OUTER JOIN` between expected and
invoiced amounts finds revenue without an invoice and invoices without revenue.
`NULL` cases are spelled out in the `WHERE` clause, because `x <> NULL` is
`NULL`, not true, and would let a missing invoice pass silently.

**Keep orphans visible.** The payout check uses a `LEFT JOIN` from statements to
revenue: an inner join would drop a statement that has no revenue behind it,
which is exactly the case to report.

## Contract

Each query returns one row per problem; an empty result means reconciled.
Columns, in order: `check_name, subject, expected_eur, actual_eur`. The month
comes from the one-row table `billing_period` (`month_label, period_start,
period_end`), so the queries take no parameters and run unchanged on DuckDB and
Snowflake.

## Tests

[`tests/test_reconcile.py`](../tests/test_reconcile.py): 12 tests. Each one
breaks the data in one realistic way (an invoice off by a cent, a missing
invoice, revenue changed after invoicing, a revenue share typo, …) and expects
the check to report it, plus a test that correct data reconciles cleanly.

```bash
uv run pytest tests/test_reconcile.py -v
```

When a check fails in production, see [runbook](runbook.md), section 6.
