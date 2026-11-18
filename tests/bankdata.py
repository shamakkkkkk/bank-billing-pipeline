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
