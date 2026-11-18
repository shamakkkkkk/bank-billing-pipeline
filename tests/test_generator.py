import csv
import random
from datetime import date
from decimal import Decimal

import pytest

from savehub import generator as g

SEED = 42


@pytest.fixture(scope="module")
def customers():
    return g.make_customers(g.load_banks(), g.load_partner_ids(), SEED)


def test_one_file_per_bank_per_day(customers):
    files = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 31), SEED)
    assert len(files) == 31 * 3
    assert len({f.filename for f in files}) == len(files)
    assert files[0].filename == "bank_alpha_2026-10-01.csv"


def test_every_customer_in_exactly_one_file_per_day(customers):
    files = g.build_files(customers, date(2026, 10, 5), date(2026, 10, 5), SEED)
    ids = [r["customer_id"] for f in files for r in f.rows]
    assert sorted(ids) == sorted(c.customer_id for c in customers)


def test_balances_are_valid_money(customers):
    files = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 31), SEED)
    for f in files:
        for r in f.rows:
            amount = Decimal(r["balance_eur"])
            assert amount >= 0
            assert amount.as_tuple().exponent == -2  # always exactly 2 decimals
            assert r["date"] == f.day.isoformat()


def test_deterministic(customers, tmp_path):
    a = g.write_files(
        g.build_files(customers, date(2026, 10, 1), date(2026, 10, 3), SEED), tmp_path / "a"
    )
    b = g.write_files(
        g.build_files(customers, date(2026, 10, 1), date(2026, 10, 3), SEED), tmp_path / "b"
    )
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


def test_single_day_matches_month_run(customers):
    """Backfilling one day must produce the same data as the original month run."""
    month = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 31), SEED)
    day = g.build_files(customers, date(2026, 10, 17), date(2026, 10, 17), SEED)
    assert [f.rows for f in day] == [f.rows for f in month if f.day == date(2026, 10, 17)]


def test_balances_move_over_time(customers):
    files = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 31), SEED)
    first = {r["customer_id"]: r["balance_eur"] for f in files[:3] for r in f.rows}
    last = {r["customer_id"]: r["balance_eur"] for f in files[-3:] for r in f.rows}
    assert first != last


def test_some_customers_have_no_partner(customers):
    partners = {c.partner_id for c in customers}
    assert None in partners
    assert {"P01", "P02"} <= partners


def test_cannot_generate_before_epoch(customers):
    with pytest.raises(ValueError):
        g.build_files(customers, date(2024, 12, 31), date(2024, 12, 31), SEED)


@pytest.mark.parametrize("defect", g.DEFECTS)
def test_each_defect_breaks_the_file(customers, defect):
    good = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 1), SEED)[0]
    bad = g.corrupt(good, defect, random.Random(0))
    assert bad.defect == defect
    assert (bad.header, bad.rows) != (good.header, good.rows)
    assert good.rows[0]["balance_eur"] != ""  # original untouched


def test_inject_defects_count(customers, tmp_path):
    files = g.build_files(customers, date(2026, 10, 1), date(2026, 10, 10), SEED)
    files = g.inject_defects(files, 4, SEED)
    assert sum(f.defect is not None for f in files) == 4

    # missing_column files must still be readable CSV with the reduced header
    for path, f in zip(g.write_files(files, tmp_path), files, strict=True):
        with open(path, newline="") as fh:
            assert csv.DictReader(fh).fieldnames == f.header


def test_cli_no_upload(tmp_path, capsys):
    g.main(["--date", "2026-10-01", "--out", str(tmp_path), "--no-upload"])
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "bank_alpha_2026-10-01.csv",
        "bank_beta_2026-10-01.csv",
        "bank_gamma_2026-10-01.csv",
    ]
    assert "Wrote 3 files" in capsys.readouterr().out


def test_cli_rejects_bad_month():
    with pytest.raises(SystemExit):
        g.main(["--month", "2026-13", "--no-upload"])
