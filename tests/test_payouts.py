"""Partner payouts, on top of the daily revenue pipeline."""

from datetime import date
from decimal import Decimal

import pytest
from bankdata import BUCKET, DAY, put_validated

from savehub import daily

SHARES = {"P01": Decimal("0.20"), "P02": Decimal("0.15")}


def run_day(wh, s3, day=DAY):
    daily.load_balances(wh, s3, day, BUCKET)
    daily.calculate_revenue(wh, day)
    return daily.calculate_partner_payouts(wh, day)


def test_payout_is_revenue_times_share_exactly(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3)
    revenue = dict(
        wh.query("SELECT customer_id, revenue_eur FROM daily_revenue WHERE partner_id IS NOT NULL")
    )
    partner_of = {c.customer_id: c.partner_id for c in customers}
    expected = {cid: rev * SHARES[partner_of[cid]] for cid, rev in revenue.items()}
    assert dict(wh.query("SELECT customer_id, payout_eur FROM partner_payouts_daily")) == expected


def test_only_referred_customers_get_payouts(wh, s3, customers):
    put_validated(s3, customers, DAY)
    rows = run_day(wh, s3)
    referred = [c for c in customers if c.partner_id]
    assert rows == len(referred)
    assert 0 < rows < len(customers)  # the seed has customers with and without partner


def test_payout_never_exceeds_revenue_of_referred_customers(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3)
    over = wh.query(
        "SELECT p.partner_id FROM partner_payouts_daily p GROUP BY 1 "
        "HAVING SUM(p.payout_eur) > SUM(p.revenue_eur)"
    )
    assert over == []


def test_rerun_is_idempotent(wh, s3, customers):
    put_validated(s3, customers, DAY)
    run_day(wh, s3)
    first = wh.query("SELECT * FROM partner_payouts_daily ORDER BY ALL")
    run_day(wh, s3)
    assert wh.query("SELECT * FROM partner_payouts_daily ORDER BY ALL") == first


def test_unknown_partner_fails_and_rolls_back(wh, s3, customers):
    put_validated(s3, customers, DAY)
    daily.load_balances(wh, s3, DAY, BUCKET)
    daily.calculate_revenue(wh, DAY)
    wh.execute("DELETE FROM partners WHERE partner_id = 'P02'")
    with pytest.raises(daily.UnknownPartner, match="P02"):
        daily.calculate_partner_payouts(wh, DAY)
    assert wh.scalar("SELECT COUNT(*) FROM partner_payouts_daily") == 0


def test_other_days_untouched(wh, s3, customers):
    put_validated(s3, customers, DAY, date(2026, 10, 2))
    run_day(wh, s3, DAY)
    run_day(wh, s3, date(2026, 10, 2))
    run_day(wh, s3, DAY)
    days = wh.query("SELECT DISTINCT payout_date FROM partner_payouts_daily ORDER BY 1")
    assert days == [(DAY,), (date(2026, 10, 2),)]
