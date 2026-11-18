"""Shared test data helpers (fixtures live in conftest.py)."""

from datetime import date

from savehub import daily
from savehub import generator as g

BUCKET = "test-bucket"
SEED = 42
DAY = date(2026, 10, 1)


def put_validated(s3, customers, start, end=None, skip_banks=()):
    """Upload generator files as if the Lambda had already validated them."""
    files = g.build_files(customers, start, end or start, SEED)
    for f in files:
        if f.bank in skip_banks:
            continue
        lines = [",".join(f.header)] + [",".join(r[c] for c in f.header) for r in f.rows]
        s3.put_object(Bucket=BUCKET, Key=daily.validated_key(f.day, f.bank),
                      Body=("\n".join(lines) + "\n").encode())  # fmt: skip
    return files


def run_month(wh, s3, customers, month="2026-10"):
    """Upload a full month of validated files and run the daily pipeline for each day."""
    from savehub import monthly

    start, end = monthly.month_bounds(month)
    files = put_validated(s3, customers, start, end)
    for day in sorted({f.day for f in files}):
        daily.load_balances(wh, s3, day, BUCKET)
        daily.calculate_revenue(wh, day)
        daily.calculate_partner_payouts(wh, day)
    return files
