"""Monthly invoices, partner statements and export (not the reconciliation SQL:
that one is specified in test_reconcile.py)."""

import csv
import io
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from bankdata import BUCKET, run_month

from savehub import monthly

MONTH = "2026-10"
CENT = Decimal("0.01")


@pytest.fixture
def october(wh, s3, customers):
    run_month(wh, s3, customers, MONTH)
    return wh


def test_month_bounds():
    assert monthly.month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert monthly.month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_one_invoice_per_bank_rounded_once(october):
    assert monthly.create_invoices(october, MONTH) == 3
    exact = dict(
        october.query(
            "SELECT bank, SUM(revenue_eur) FROM daily_revenue "
            "WHERE revenue_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31' GROUP BY bank"
        )
    )
    invoices = october.query(
        "SELECT invoice_id, bank, amount_eur, revenue_eur_exact, customer_days, period_start, "
        "period_end FROM invoices ORDER BY bank"
    )
    for inv_id, bank, amount, exact_sum, _days, start, end in invoices:
        assert inv_id == f"INV-2026-10-{bank.upper()}"
        assert exact_sum == exact[bank]
        # the one rounding: sum exact values, then round half up to cents
        assert amount == exact[bank].quantize(CENT, rounding=ROUND_HALF_UP)
        assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert sum(r[4] for r in invoices) == 3100  # 100 customers x 31 days


def test_statements_per_partner(october):
    assert monthly.create_partner_statements(october, MONTH) == 2
    rows = october.query(
        "SELECT statement_id, partner_name, referred_customers, amount_eur, payout_eur_exact "
        "FROM partner_statements ORDER BY partner_id"
    )
    assert [r[0] for r in rows] == ["STM-2026-10-P01", "STM-2026-10-P02"]
    assert [r[1] for r in rows] == ["MoneyTips", "FinBlog"]
    for *_, amount, exact in rows:
        assert amount == exact.quantize(CENT, rounding=ROUND_HALF_UP)


def test_rerun_month_is_idempotent(october):
    def snapshot():
        return (
            october.query("SELECT * EXCLUDE (created_at) FROM invoices ORDER BY ALL"),
            october.query("SELECT * EXCLUDE (created_at) FROM partner_statements ORDER BY ALL"),
        )

    monthly.create_invoices(october, MONTH)
    monthly.create_partner_statements(october, MONTH)
    first = snapshot()
    monthly.create_invoices(october, MONTH)
    monthly.create_partner_statements(october, MONTH)
    assert snapshot() == first


def test_incomplete_month_is_not_invoiced(october):
    october.execute(
        "DELETE FROM daily_revenue WHERE revenue_date = DATE '2026-10-17' AND bank = 'beta'"
    )
    with pytest.raises(monthly.MonthIncomplete, match="2026-10-17: no revenue for beta"):
        monthly.create_invoices(october, MONTH)
    assert october.scalar("SELECT COUNT(*) FROM invoices") == 0


def test_missing_payouts_block_statements(october):
    october.execute("DELETE FROM partner_payouts_daily WHERE payout_date = DATE '2026-10-09'")
    with pytest.raises(monthly.MonthIncomplete, match="2026-10-09"):
        monthly.create_partner_statements(october, MONTH)


def test_reconcile_refuses_to_run_on_stub(october, monkeypatch):
    monkeypatch.setattr(monthly, "read_sql", lambda name: "-- TODO\nSELECT 1")
    with pytest.raises(monthly.ReconciliationNotWritten):
        monthly.reconcile(october, MONTH)


def test_reconcile_raises_with_readable_problems(october, monkeypatch):
    fake = "SELECT 'invoice_total' AS c, 'alpha' AS s, 1.00 AS e, 1.01 AS a"
    monkeypatch.setattr(monthly, "read_sql", lambda name: fake)
    with pytest.raises(monthly.ReconciliationFailed, match="invoice_total: alpha expected 1.00"):
        monthly.reconcile(october, MONTH)


def test_export_writes_one_csv_per_document(october, s3):
    monthly.create_invoices(october, MONTH)
    monthly.create_partner_statements(october, MONTH)
    keys = monthly.export(october, s3, MONTH, BUCKET)
    assert keys == [
        "invoices/2026-10/INV-2026-10-ALPHA.csv",
        "invoices/2026-10/INV-2026-10-BETA.csv",
        "invoices/2026-10/INV-2026-10-GAMMA.csv",
        "statements/2026-10/STM-2026-10-P01.csv",
        "statements/2026-10/STM-2026-10-P02.csv",
    ]
    body = s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read().decode()
    (row,) = list(csv.DictReader(io.StringIO(body)))
    db_amount = october.scalar("SELECT amount_eur FROM invoices WHERE bank = 'alpha'")
    assert row["amount_eur"] == str(db_amount)  # "579.18": cents, no float formatting
    assert row["currency"] == "EUR"


def test_export_rerun_removes_stale_files(october, s3):
    monthly.create_invoices(october, MONTH)
    monthly.create_partner_statements(october, MONTH)
    s3.put_object(Bucket=BUCKET, Key="invoices/2026-10/INV-2026-10-OLD.csv", Body=b"x")
    monthly.export(october, s3, MONTH, BUCKET)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="invoices/")["Contents"]]
    assert "invoices/2026-10/INV-2026-10-OLD.csv" not in keys
    assert len(keys) == 3
