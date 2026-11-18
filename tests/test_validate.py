"""Business rules of the validation Lambda. No AWS involved: validate() is pure."""

import csv
import io
import random
from datetime import date

import pytest
import validate as v

from savehub import generator as g

BANKS = {"alpha", "beta", "gamma"}
GOOD_NAME = "bank_alpha_2026-10-01.csv"


def to_csv(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture(scope="module")
def good_file() -> g.BankFile:
    customers = g.make_customers(g.load_banks(), g.load_partner_ids(), 42)
    return g.build_files(customers, date(2026, 10, 1), date(2026, 10, 1), 42)[0]


def good_rows():
    return [
        {"date": "2026-10-01", "bank": "alpha", "customer_id": "C0001", "partner_id": "P01",
         "balance_eur": "100.00"},
        {"date": "2026-10-01", "bank": "alpha", "customer_id": "C0002", "partner_id": "",
         "balance_eur": "0.00"},
    ]  # fmt: skip


def check(rows, name=GOOD_NAME, header=None):
    return v.validate(name, to_csv(header or g.HEADER, rows), BANKS)


# --- generator files ------------------------------------------------------------


def test_generated_file_is_valid(good_file):
    assert v.validate(good_file.filename, to_csv(good_file.header, good_file.rows), BANKS) == []


@pytest.mark.parametrize(
    "defect, expected",
    [
        ("missing_column", "missing column(s): balance_eur"),
        ("negative_balance", "is negative"),
        ("empty_balance", "balance_eur is empty"),
        ("not_a_number", "decimal comma"),
        ("date_mismatch", "does not match file name date"),
    ],
)
def test_every_generator_defect_is_caught(good_file, defect, expected):
    """Contract between generator and Lambda: each simulated defect gets rejected."""
    bad = g.corrupt(good_file, defect, random.Random(0))
    errors = v.validate(bad.filename, to_csv(bad.header, bad.rows), BANKS)
    assert errors, f"{defect} was not rejected"
    assert any(expected in e for e in errors), errors


def test_defects_list_is_fully_covered():
    # If someone adds a defect to the generator, the test above must grow too.
    assert len(g.DEFECTS) == 5


# --- file level -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["alpha_2026-10-01.csv", "bank_alpha_2026-10-01.txt", "bank_Alpha_2026-10-01.csv",
     "bank_alpha_01-10-2026.csv"],
)  # fmt: skip
def test_bad_file_names(name):
    assert "does not match" in check(good_rows(), name=name)[0]


def test_impossible_date_in_name():
    assert "invalid date" in check(good_rows(), name="bank_alpha_2026-02-30.csv")[0]


def test_unknown_bank():
    errors = check(good_rows(), name="bank_delta_2026-10-01.csv")
    assert errors == ["unknown bank 'delta', expected one of: alpha, beta, gamma"]


def test_empty_file():
    assert v.validate(GOOD_NAME, b"", BANKS) == ["file is empty"]


def test_header_only():
    assert check([]) == ["file has a header but no rows"]


def test_not_utf8():
    assert v.validate(GOOD_NAME, "date\n\xe9".encode("latin-1"), BANKS) == [
        "file is not valid UTF-8 text"
    ]


def test_excel_bom_is_accepted():
    assert v.validate(GOOD_NAME, b"\xef\xbb\xbf" + to_csv(g.HEADER, good_rows()), BANKS) == []


def test_columns_in_other_order_are_fine():
    assert check(good_rows(), header=list(reversed(g.HEADER))) == []


def test_extra_column():
    rows = [r | {"iban": "DE00"} for r in good_rows()]
    assert check(rows, header=[*g.HEADER, "iban"]) == ["unexpected column(s): iban"]


def test_duplicate_column():
    content = b"date,bank,customer_id,partner_id,balance_eur,balance_eur\n"
    assert v.validate(GOOD_NAME, content, BANKS) == ["duplicate column names in header"]


# --- row level --------------------------------------------------------------------


def test_duplicate_customer():
    rows = good_rows()
    rows[1]["customer_id"] = "C0001"
    assert check(rows) == ["line 3: customer C0001 already appears on line 2"]


def test_wrong_bank_in_rows():
    rows = good_rows()
    rows[0]["bank"] = "beta"
    assert "does not match file name bank" in check(rows)[0]


def test_empty_customer():
    rows = good_rows()
    rows[0]["customer_id"] = " "
    assert check(rows) == ["line 2: customer_id is empty"]


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1e5", "12.345", " 12.00", "12.", "€12"])
def test_amounts_decimal_would_accept_or_misread(amount):
    rows = good_rows()
    rows[0]["balance_eur"] = amount
    assert "is not an amount like 1234.56" in check(rows)[0]


@pytest.mark.parametrize("amount", ["0", "5", "5.5", "1234.56"])
def test_valid_amounts(amount):
    rows = good_rows()
    rows[0]["balance_eur"] = amount
    assert check(rows) == []


def test_ragged_rows():
    content = to_csv(g.HEADER, good_rows()) + b"2026-10-01,alpha,C0003\n" + b"a,b,c,d,e,f\n"
    assert v.validate(GOOD_NAME, content, BANKS) == [
        "line 4: fewer values than columns",
        "line 5: more values than columns",
    ]


def test_error_list_is_capped():
    rows = [r | {"balance_eur": ""} for r in good_rows() * 30]
    for i, r in enumerate(rows):
        r["customer_id"] = f"C{i:04d}"
    errors = check(rows)
    assert len(errors) == v.MAX_ERRORS + 1
    assert errors[-1] == f"stopped after {v.MAX_ERRORS} problems"
