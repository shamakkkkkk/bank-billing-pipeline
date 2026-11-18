"""Daily pipeline against DuckDB in memory and moto's fake S3."""

from datetime import date
from decimal import Decimal

import pytest
from bankdata import BUCKET, DAY, put_validated

from savehub import daily
from savehub.warehouse import DuckDBWarehouse


def run_day(wh, s3, day):
    daily.load_balances(wh, s3, day, BUCKET)
    return daily.calculate_revenue(wh, day)


# --- the money -----------------------------------------------------------------


def test_daily_rate_is_exact_decimal():
    assert daily.daily_fee_rate(Decimal("0.005")) == Decimal("0.000013698630")
    assert daily.daily_fee_rate(Decimal("0.007")) == Decimal("0.000019178082")


def test_revenue_matches_independent_python_calculation(wh, s3, customers):
    files = put_validated(s3, customers, DAY)
    assert run_day(wh, s3, DAY) == len(customers)

    rates = {"alpha": Decimal("0.003"), "beta": Decimal("0.005"), "gamma": Decimal("0.007")}
    expected = {
        r["customer_id"]: Decimal(r["balance_eur"]) * daily.daily_fee_rate(rates[f.bank])
        for f in files
        for r in f.rows
    }
    actual = dict(wh.query("SELECT customer_id, revenue_eur FROM daily_revenue"))
    assert actual == expected  # exact, to the last decimal


def test_revenue_column_is_decimal_not_float(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3, DAY)
    (value,) = wh.query("SELECT revenue_eur FROM daily_revenue LIMIT 1")[0]
    assert isinstance(value, Decimal)
    assert wh.scalar("SELECT typeof(SUM(revenue_eur)) FROM daily_revenue").startswith("DECIMAL")


def test_rate_change_mid_month_applies_from_valid_from(wh, s3, customers):
    wh.execute(
        "INSERT INTO bank_rates VALUES ('alpha', 0.004, ?, DATE '2026-10-02')",
        [daily.daily_fee_rate(Decimal("0.004"))],
    )
    put_validated(s3, customers, DAY, date(2026, 10, 2))
    run_day(wh, s3, DAY)
    run_day(wh, s3, date(2026, 10, 2))
    rates = wh.query(
        "SELECT revenue_date, MIN(annual_fee_rate), MAX(annual_fee_rate) FROM daily_revenue "
        "WHERE bank = 'alpha' GROUP BY revenue_date ORDER BY revenue_date"
    )
    assert rates == [
        (date(2026, 10, 1), Decimal("0.003"), Decimal("0.003")),
        (date(2026, 10, 2), Decimal("0.004"), Decimal("0.004")),
    ]


# --- idempotency and backfill -------------------------------------------------


def table_snapshot(wh):
    return (
        wh.query("SELECT * EXCLUDE (loaded_at) FROM balances ORDER BY ALL"),
        wh.query("SELECT * FROM daily_revenue ORDER BY ALL"),
    )


def test_running_a_day_twice_changes_nothing(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3, DAY)
    first = table_snapshot(wh)
    run_day(wh, s3, DAY)
    assert table_snapshot(wh) == first
    assert wh.scalar("SELECT COUNT(*) FROM balances") == len(customers)


def test_backfill_out_of_order_gives_same_result(wh, s3, customers):
    put_validated(s3, customers, DAY, date(2026, 10, 3))
    for d in (3, 1, 2):  # backfill in any order
        run_day(wh, s3, date(2026, 10, d))
    out_of_order = table_snapshot(wh)

    with DuckDBWarehouse(":memory:") as fresh:
        daily.prepare(fresh)
        for d in (1, 2, 3):
            run_day(fresh, s3, date(2026, 10, d))
        assert table_snapshot(fresh) == out_of_order


def test_rerun_one_day_leaves_other_days_alone(wh, s3, customers):
    put_validated(s3, customers, DAY, date(2026, 10, 2))
    run_day(wh, s3, DAY)
    run_day(wh, s3, date(2026, 10, 2))
    run_day(wh, s3, DAY)
    counts = wh.query("SELECT revenue_date, COUNT(*) FROM daily_revenue GROUP BY 1 ORDER BY 1")
    assert counts == [(DAY, len(customers)), (date(2026, 10, 2), len(customers))]


def test_corrected_file_replaces_old_rows(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3, DAY)
    key = daily.validated_key(DAY, "alpha")
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode().splitlines()
    first_customer = body[1].split(",")[2]
    body[1] = ",".join(body[1].split(",")[:4] + ["999.99"])
    s3.put_object(Bucket=BUCKET, Key=key, Body=("\n".join(body) + "\n").encode())

    run_day(wh, s3, DAY)
    assert wh.scalar(
        "SELECT balance_eur FROM balances WHERE customer_id = ?", [first_customer]
    ) == Decimal("999.99")
    assert wh.scalar("SELECT COUNT(*) FROM balances") == len(customers)


# --- failures must be loud --------------------------------------------------------


def test_missing_bank_file_fails_and_loads_nothing(wh, s3, customers):
    put_validated(s3, customers, DAY, skip_banks={"beta"})
    with pytest.raises(daily.MissingBankFiles, match="beta.*rejected/"):
        daily.load_balances(wh, s3, DAY, BUCKET)
    assert wh.scalar("SELECT COUNT(*) FROM balances") == 0  # all or nothing


def test_bank_not_yet_onboarded_is_not_expected(wh, s3, customers):
    wh.execute("UPDATE bank_rates SET valid_from = DATE '2026-11-01' WHERE bank = 'gamma'")
    assert daily.expected_banks(wh, DAY) == ["alpha", "beta"]


def test_balance_without_rate_fails_instead_of_dropping_revenue(wh, s3, customers):
    put_validated(s3, customers, DAY)
    daily.load_balances(wh, s3, DAY, BUCKET)
    wh.execute("DELETE FROM bank_rates WHERE bank = 'gamma'")
    with pytest.raises(daily.RevenueIncomplete, match="no rate"):
        daily.calculate_revenue(wh, DAY)
    assert wh.scalar("SELECT COUNT(*) FROM daily_revenue") == 0  # rolled back


def test_prepare_is_idempotent(wh):
    daily.prepare(wh)
    daily.prepare(wh)
    assert wh.scalar("SELECT COUNT(*) FROM bank_rates") == 3
    assert wh.scalar("SELECT COUNT(*) FROM partners") == 2
