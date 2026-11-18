"""The same pipeline on DuckDB and on Snowflake must produce the same money.

Runs October 2026 end to end on both engines and compares every table,
to the last decimal. Uses a throwaway schema that is dropped afterwards.

Needs the Snowflake settings in .env (see .env.example). Uses a few cents of
credits on an XSMALL warehouse:
    uv run --env-file .env pytest -m snowflake -v
"""

import os
import uuid
from datetime import date

import pytest
from bankdata import DAY, SEED, run_month

from savehub import daily, monthly
from savehub import generator as g
from savehub.warehouse import DuckDBWarehouse, SnowflakeWarehouse

pytestmark = pytest.mark.snowflake

MONTH = "2026-10"
BUCKET = "test-bucket"


class FakeS3:
    """Just enough of the S3 client for load_balances. Keeps this test free of
    moto, which patches HTTP and must not intercept the Snowflake driver."""

    class exceptions:  # noqa: N801 (mirrors boto3's client.exceptions)
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 (boto3 argument names)
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        body = self.objects[Key]
        return {"Body": type("Body", (), {"read": lambda self: body})()}


TABLES = {
    "balances": "SELECT balance_date, bank, customer_id, partner_id, balance_eur "
    "FROM balances ORDER BY 1, 2, 3",
    "daily_revenue": "SELECT * FROM daily_revenue ORDER BY 1, 2, 3",
    "partner_payouts_daily": "SELECT * FROM partner_payouts_daily ORDER BY 1, 2, 3, 4",
    "invoices": "SELECT invoice_id, invoice_month, bank, period_start, period_end, "
    "customer_days, revenue_eur_exact, amount_eur, currency FROM invoices ORDER BY 1",
    "partner_statements": "SELECT statement_id, statement_month, partner_id, partner_name, "
    "period_start, period_end, referred_customers, referred_revenue_eur, payout_eur_exact, "
    "amount_eur, currency FROM partner_statements ORDER BY 1",
}


def run_october(wh, s3, customers):
    daily.prepare(wh)
    run_month(wh, s3, customers, MONTH)
    monthly.create_invoices(wh, MONTH)
    monthly.create_partner_statements(wh, MONTH)
    monthly.reconcile(wh, MONTH)  # raises if anything does not match


@pytest.fixture(scope="module")
def customers():
    return g.make_customers(g.load_banks(), g.load_partner_ids(), SEED)


@pytest.fixture(scope="module")
def engines(customers):
    if not os.environ.get("SNOWFLAKE_ACCOUNT"):
        pytest.skip("SNOWFLAKE_ACCOUNT is empty: fill the Snowflake settings in .env")
    s3 = FakeS3()
    duck = DuckDBWarehouse(":memory:")
    run_october(duck, s3, customers)

    schema = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    snow = SnowflakeWarehouse(schema=schema)
    snow.execute(f"CREATE SCHEMA {schema}")
    snow.execute(f"USE SCHEMA {schema}")
    try:
        run_october(snow, s3, customers)
        yield duck, snow, s3
    finally:
        snow.execute(f"DROP SCHEMA IF EXISTS {schema}")
        snow.close()
        duck.close()


@pytest.mark.parametrize("table", TABLES)
def test_same_rows_on_duckdb_and_snowflake(engines, table):
    duck, snow, _ = engines
    duck_rows = duck.query(TABLES[table])
    snow_rows = snow.query(TABLES[table])
    assert len(snow_rows) == len(duck_rows) > 0
    assert snow_rows == duck_rows  # every value, Decimal to the last digit


def test_invoice_amounts_match(engines):
    duck, snow, _ = engines
    sql = "SELECT invoice_id, amount_eur FROM invoices ORDER BY 1"
    assert snow.query(sql) == duck.query(sql)
    print("\nSnowflake invoices:", snow.query(sql))


def test_rerunning_a_day_on_snowflake_changes_nothing(engines):
    _, snow, s3 = engines
    before = snow.query(TABLES["daily_revenue"])
    daily.load_balances(snow, s3, DAY, BUCKET)
    daily.calculate_revenue(snow, DAY)
    daily.calculate_partner_payouts(snow, DAY)
    assert snow.query(TABLES["daily_revenue"]) == before


def test_failed_check_rolls_back_on_snowflake(engines):
    """BEGIN/ROLLBACK really works: a failing revenue check leaves no partial rows."""
    _, snow, _ = engines
    day = date(2026, 10, 15)
    before = snow.query("SELECT COUNT(*) FROM daily_revenue WHERE revenue_date = ?", [day])
    snow.execute("DELETE FROM bank_rates WHERE bank = 'gamma'")
    try:
        with pytest.raises(daily.RevenueIncomplete):
            daily.calculate_revenue(snow, day)
        after = snow.query("SELECT COUNT(*) FROM daily_revenue WHERE revenue_date = ?", [day])
        assert after == before
    finally:
        daily.prepare(snow)  # restore the reference data
