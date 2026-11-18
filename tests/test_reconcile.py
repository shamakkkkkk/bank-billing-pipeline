"""Specification for the reconciliation SQL (sql/monthly/reconcile_*.sql).

Written before the SQL exists: these tests are skipped while the files are
TODO stubs, and must all pass once they are written. Each test breaks the
data in one realistic way and expects the reconciliation to notice.
Run just these:  uv run pytest tests/test_reconcile.py -v
"""

import pytest
from bankdata import run_month

from savehub import monthly
from savehub.warehouse import read_sql

MONTH = "2026-10"


def _is_stub(name: str) -> bool:
    return "TODO" in read_sql(name)


needs_invoice_check = pytest.mark.skipif(
    _is_stub("monthly/reconcile_invoices"), reason="reconcile_invoices.sql not written yet"
)
needs_payout_check = pytest.mark.skipif(
    _is_stub("monthly/reconcile_partner_payouts"),
    reason="reconcile_partner_payouts.sql not written yet",
)


@pytest.fixture
def invoiced(wh, s3, customers):
    run_month(wh, s3, customers, MONTH)
    monthly.create_invoices(wh, MONTH)
    monthly.create_partner_statements(wh, MONTH)
    return wh


def problems(wh, name):
    monthly.set_billing_period(wh, MONTH)
    return wh.query(read_sql(name))


# --- check 1: invoices == daily revenue ---------------------------------------


@needs_invoice_check
def test_invoices_reconcile_on_correct_data(invoiced):
    assert problems(invoiced, "monthly/reconcile_invoices") == []


@needs_invoice_check
def test_result_has_the_contract_columns(invoiced):
    invoiced.execute("UPDATE invoices SET amount_eur = amount_eur + 1 WHERE bank = 'alpha'")
    rows = problems(invoiced, "monthly/reconcile_invoices")
    assert rows and all(len(r) == 4 for r in rows)  # check_name, subject, expected, actual


@needs_invoice_check
def test_detects_one_cent_difference(invoiced):
    invoiced.execute("UPDATE invoices SET amount_eur = amount_eur + 0.01 WHERE bank = 'beta'")
    rows = problems(invoiced, "monthly/reconcile_invoices")
    assert any("beta" in str(r[1]) for r in rows), rows


@needs_invoice_check
def test_detects_missing_invoice(invoiced):
    invoiced.execute("DELETE FROM invoices WHERE bank = 'gamma'")
    rows = problems(invoiced, "monthly/reconcile_invoices")
    assert any("gamma" in str(r[1]) for r in rows), rows


@needs_invoice_check
def test_detects_revenue_added_after_invoicing(invoiced):
    """Late data: a day was re-run with a corrected file after the invoice was made."""
    invoiced.execute(
        "UPDATE daily_revenue SET revenue_eur = revenue_eur + 5 "
        "WHERE revenue_date = DATE '2026-10-20' AND customer_id = "
        "(SELECT MIN(customer_id) FROM daily_revenue WHERE bank = 'alpha')"
    )
    rows = problems(invoiced, "monthly/reconcile_invoices")
    assert any("alpha" in str(r[1]) for r in rows), rows


@needs_invoice_check
def test_other_months_do_not_count(invoiced):
    """Revenue outside the billing period must not affect October's check."""
    invoiced.execute(
        "INSERT INTO daily_revenue SELECT DATE '2026-11-01', bank, customer_id, partner_id, "
        "balance_eur, annual_fee_rate, daily_fee_rate, revenue_eur "
        "FROM daily_revenue WHERE revenue_date = DATE '2026-10-31'"
    )
    assert problems(invoiced, "monthly/reconcile_invoices") == []


# --- check 2: partner payout <= revenue from its customers -----------------------


@needs_payout_check
def test_payouts_reconcile_on_correct_data(invoiced):
    assert problems(invoiced, "monthly/reconcile_partner_payouts") == []


@needs_payout_check
def test_detects_statement_above_referred_revenue(invoiced):
    invoiced.execute("UPDATE partner_statements SET amount_eur = 100000 WHERE partner_id = 'P01'")
    rows = problems(invoiced, "monthly/reconcile_partner_payouts")
    assert any("P01" in str(r[1]) for r in rows), rows


@needs_payout_check
def test_uses_revenue_table_not_the_statement_itself(invoiced):
    """The limit must come from daily_revenue (the source of truth), not from the
    referred_revenue_eur column that the same statement query wrote."""
    invoiced.execute(
        "UPDATE partner_statements SET amount_eur = 100000, referred_revenue_eur = 200000 "
        "WHERE partner_id = 'P02'"
    )
    rows = problems(invoiced, "monthly/reconcile_partner_payouts")
    assert any("P02" in str(r[1]) for r in rows), rows


@needs_payout_check
def test_share_above_100_percent_is_caught(invoiced):
    """A typo in seeds (2.0 instead of 0.20) makes every payout exceed revenue."""
    invoiced.execute("UPDATE partners SET revenue_share = 2.0 WHERE partner_id = 'P01'")
    invoiced.execute(
        "UPDATE partner_payouts_daily SET revenue_share = 2.0, payout_eur = revenue_eur * 2 "
        "WHERE partner_id = 'P01'"
    )
    monthly.create_partner_statements(invoiced, MONTH)
    rows = problems(invoiced, "monthly/reconcile_partner_payouts")
    assert any("P01" in str(r[1]) for r in rows), rows
