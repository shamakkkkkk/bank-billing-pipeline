"""Simulate the daily balance files that partner banks send to SaveHub.

One file per bank per day, e.g. ``bank_alpha_2026-10-01.csv``::

    date,bank,customer_id,partner_id,balance_eur
    2026-10-01,alpha,C0001,P01,12345.67

Design choices:
- Deterministic: same --seed gives byte-identical files, so tests and bug
  reports are reproducible.
- Each day's balance moves come from an RNG seeded by (seed, date), and the
  walk always starts at EPOCH. So generating one day alone gives exactly the
  same file as generating the whole month -- useful for backfills.
- Money is integer cents internally and Decimal when printed. No floats.
- Can deliberately produce broken files so the validation Lambda has
  something to reject.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from savehub import config

EPOCH = date(2025, 1, 1)  # all balance walks start here; earliest date we can generate
NUM_CUSTOMERS = 100
HEADER = ["date", "bank", "customer_id", "partner_id", "balance_eur"]

DEFECTS = (
    "missing_column",  # balance_eur column dropped
    "negative_balance",  # one row has a negative balance
    "empty_balance",  # one row has no balance
    "not_a_number",  # one row uses a decimal comma: "12345,67"
    "date_mismatch",  # rows are dated one day before the file name
)


@dataclass(frozen=True)
class Customer:
    customer_id: str
    bank: str
    partner_id: str | None
    opening_balance_cents: int


@dataclass(frozen=True)
class BankFile:
    bank: str
    day: date
    header: list[str]
    rows: list[dict[str, str]]
    defect: str | None = None

    @property
    def filename(self) -> str:
        return f"bank_{self.bank}_{self.day.isoformat()}.csv"


# --- reference data ---------------------------------------------------------


def load_banks(seeds_dir: Path = config.SEEDS_DIR) -> list[str]:
    with open(seeds_dir / "bank_rates.csv", newline="") as f:
        return sorted({row["bank"] for row in csv.DictReader(f)})


def load_partner_ids(seeds_dir: Path = config.SEEDS_DIR) -> list[str]:
    with open(seeds_dir / "partners.csv", newline="") as f:
        return sorted(row["partner_id"] for row in csv.DictReader(f))


def make_customers(
    banks: list[str], partner_ids: list[str], seed: int, n: int = NUM_CUSTOMERS
) -> list[Customer]:
    """Fixed customer base: each customer has one bank and at most one partner, forever."""
    rng = random.Random(f"customers-{seed}")
    customers = []
    for i in range(1, n + 1):
        bank = rng.choice(banks)
        # Two "None" slots: with 2 partners, half of the customers came without a partner.
        partner = rng.choice([*partner_ids, None, None])
        opening = rng.randint(500_00, 150_000_00)  # 500 to 150k EUR, in cents
        customers.append(Customer(f"C{i:04d}", bank, partner, opening))
    return customers


# --- balances -----------------------------------------------------------------


def iter_balances(
    customers: list[Customer], start: date, end: date, seed: int
) -> Iterator[tuple[date, dict[str, int]]]:
    """Yield (day, {customer_id: balance_cents}) for start..end inclusive."""
    if start < EPOCH:
        raise ValueError(f"Cannot generate before {EPOCH}")
    balances = {c.customer_id: c.opening_balance_cents for c in customers}
    day = EPOCH
    while day <= end:
        rng = random.Random(f"balances-{seed}-{day.isoformat()}")
        for c in customers:
            if rng.random() < 0.10:  # ~10% of customers move money on a given day
                delta = rng.randint(-5_000_00, 5_000_00)  # symmetric, so no long-term drift
                balances[c.customer_id] = max(0, balances[c.customer_id] + delta)
        if day >= start:
            yield day, dict(balances)
        day += timedelta(days=1)


def format_eur(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def build_files(customers: list[Customer], start: date, end: date, seed: int) -> list[BankFile]:
    banks = sorted({c.bank for c in customers})
    files = []
    for day, balances in iter_balances(customers, start, end, seed):
        for bank in banks:
            rows = [
                {
                    "date": day.isoformat(),
                    "bank": bank,
                    "customer_id": c.customer_id,
                    "partner_id": c.partner_id or "",
                    "balance_eur": format_eur(balances[c.customer_id]),
                }
                for c in customers
                if c.bank == bank
            ]
            files.append(BankFile(bank, day, list(HEADER), rows))
    return files


# --- deliberately broken files ---------------------------------------------


def corrupt(file: BankFile, defect: str, rng: random.Random) -> BankFile:
    header = list(file.header)
    rows = [dict(r) for r in file.rows]
    target = rng.randrange(len(rows))
    match defect:
        case "missing_column":
            header.remove("balance_eur")
            for r in rows:
                del r["balance_eur"]
        case "negative_balance":
            rows[target]["balance_eur"] = "-" + rows[target]["balance_eur"]
        case "empty_balance":
            rows[target]["balance_eur"] = ""
        case "not_a_number":
            rows[target]["balance_eur"] = rows[target]["balance_eur"].replace(".", ",")
        case "date_mismatch":
            wrong = (file.day - timedelta(days=1)).isoformat()
            for r in rows:
                r["date"] = wrong
        case _:
            raise ValueError(f"Unknown defect {defect!r}")
    return BankFile(file.bank, file.day, header, rows, defect)


def inject_defects(files: list[BankFile], count: int, seed: int) -> list[BankFile]:
    if count > len(files):
        raise ValueError(f"Asked for {count} bad files but only {len(files)} files exist")
    rng = random.Random(f"defects-{seed}-{files[0].day}-{files[-1].day}")
    bad = set(rng.sample(range(len(files)), count))
    return [corrupt(f, rng.choice(DEFECTS), rng) if i in bad else f for i, f in enumerate(files)]


# --- output -----------------------------------------------------------------


def write_files(files: list[BankFile], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in files:
        path = out_dir / f.filename
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=f.header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(f.rows)
        paths.append(path)
    return paths


def upload(paths: list[Path], bucket: str, prefix: str = config.INCOMING_PREFIX) -> None:
    import boto3  # imported lazily so --no-upload works without AWS set up

    s3 = boto3.client("s3")
    for p in paths:
        s3.upload_file(str(p), bucket, prefix + p.name)


# --- CLI --------------------------------------------------------------------


def parse_month(value: str) -> tuple[date, date]:
    try:
        year, month = (int(x) for x in value.split("-"))
        first = date(year, month, 1)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {value!r}") from e
    return first, date(year, month, calendar.monthrange(year, month)[1])


def parse_day(value: str) -> tuple[date, date]:
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from e
    return d, d


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate bank balance files and upload to S3.")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--month", type=parse_month, help="YYYY-MM: one file per bank per day")
    when.add_argument("--date", type=parse_day, help="YYYY-MM-DD: a single day")
    p.add_argument("--bad-files", type=int, default=0, help="how many files to corrupt")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=config.PROJECT_ROOT / "data" / "generated")
    p.add_argument("--no-upload", action="store_true", help="only write files locally")
    args = p.parse_args(argv)

    start, end = args.month or args.date
    customers = make_customers(load_banks(), load_partner_ids(), args.seed)
    files = build_files(customers, start, end, args.seed)
    if args.bad_files:
        files = inject_defects(files, args.bad_files, args.seed)

    paths = write_files(files, args.out)
    print(f"Wrote {len(paths)} files ({start} .. {end}) to {args.out}")
    for f in files:
        if f.defect:
            print(f"  BAD  {f.filename}: {f.defect}")

    if not args.no_upload:
        bucket = config.s3_bucket()
        upload(paths, bucket)
        print(f"Uploaded to s3://{bucket}/{config.INCOMING_PREFIX}")


if __name__ == "__main__":
    main()
