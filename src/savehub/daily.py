"""Daily pipeline steps. Plain functions: the Airflow DAG only calls them, the
tests call them directly (DuckDB in memory + fake S3), no Airflow needed.

Idempotency: every step first deletes what it is about to write for that
date (and bank), then inserts, inside one transaction. Running a day twice,
or backfilling it a month later, gives exactly the same rows.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from savehub import config
from savehub.warehouse import Warehouse

# One documented place for rate precision: annual / 365 at 12 decimals.
# The error is < 0.000000000001 EUR per euro of balance per day: for a 150k
# balance under 0.0000002 EUR a day, far below the one rounding to cents.
DAILY_RATE_QUANTUM = Decimal("0.000000000001")
DAYS_PER_YEAR = 365  # simplification: leap years are billed as 365 days too


class MissingBankFiles(Exception):
    """A bank's file for the day is not in validated/ (late, or rejected)."""


class RevenueIncomplete(Exception):
    """Some balances got no revenue row: no bank rate covers that bank/date."""


def daily_fee_rate(annual: Decimal) -> Decimal:
    return (annual / DAYS_PER_YEAR).quantize(DAILY_RATE_QUANTUM, rounding=ROUND_HALF_UP)


# --- prepare -----------------------------------------------------------------


def prepare(wh: Warehouse) -> None:
    """Create tables if missing and refresh reference data from seeds/."""
    from savehub.warehouse import read_sql

    wh.execute_script(read_sql("schema"))
    with open(config.SEEDS_DIR / "bank_rates.csv", newline="") as f:
        rates = [
            (r["bank"], Decimal(r["annual_fee_rate"]),
             daily_fee_rate(Decimal(r["annual_fee_rate"])), r["valid_from"])
            for r in csv.DictReader(f)
        ]  # fmt: skip
    with open(config.SEEDS_DIR / "partners.csv", newline="") as f:
        partners = [
            (r["partner_id"], r["name"], Decimal(r["revenue_share"])) for r in csv.DictReader(f)
        ]
    # Full refresh: the seed files are the source of truth, history included
    # (a rate change is a new row with a later valid_from, never an edit).
    with wh.transaction():
        wh.execute("DELETE FROM bank_rates")
        wh.executemany(
            "INSERT INTO bank_rates (bank, annual_fee_rate, daily_fee_rate, valid_from) "
            "VALUES (?, ?, ?, ?)",
            rates,
        )
        wh.execute("DELETE FROM partners")
        wh.executemany(
            "INSERT INTO partners (partner_id, name, revenue_share) VALUES (?, ?, ?)", partners
        )


# --- load --------------------------------------------------------------------


def expected_banks(wh: Warehouse, day: date) -> list[str]:
    """Banks that must deliver a file for this day: those with a rate in force."""
    rows = wh.query(
        "SELECT DISTINCT bank FROM bank_rates WHERE valid_from <= ? ORDER BY bank", [day]
    )
    return [r[0] for r in rows]


def validated_key(day: date, bank: str) -> str:
    return f"validated/{day.isoformat()}/bank_{bank}_{day.isoformat()}.csv"


def parse_balances(content: bytes, source_file: str, loaded_at: datetime) -> list[tuple]:
    rows = []
    for r in csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline="")):
        rows.append((
            date.fromisoformat(r["date"]),
            r["bank"],
            r["customer_id"].strip(),
            r["partner_id"].strip() or None,
            Decimal(r["balance_eur"]),  # already validated by the Lambda
            source_file,
            loaded_at,
        ))  # fmt: skip
    return rows


def load_balances(wh: Warehouse, s3, day: date, bucket: str | None = None) -> dict[str, int]:
    """Load all banks' validated files for `day`. All or nothing: if any bank is
    missing, nothing is loaded and the task fails (Airflow retries later)."""
    bucket = bucket or config.s3_bucket()
    banks = expected_banks(wh, day)
    contents, missing = {}, []
    for bank in banks:
        key = validated_key(day, bank)
        try:
            contents[bank] = (key, s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except s3.exceptions.NoSuchKey:
            missing.append(bank)
    if missing:
        raise MissingBankFiles(
            f"{day}: no validated file for bank(s) {', '.join(missing)}. "
            f"Check s3://{bucket}/rejected/ for an .error.txt, or wait for the bank "
            f"to deliver. See docs/runbook.md."
        )

    loaded_at = datetime.now(UTC).replace(tzinfo=None)
    counts = {}
    with wh.transaction():
        for bank, (key, body) in contents.items():
            rows = parse_balances(body, f"s3://{bucket}/{key}", loaded_at)
            wh.execute("DELETE FROM balances WHERE balance_date = ? AND bank = ?", [day, bank])
            wh.executemany(
                "INSERT INTO balances (balance_date, bank, customer_id, partner_id, "
                "balance_eur, source_file, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            counts[bank] = len(rows)
    return counts


# --- revenue -----------------------------------------------------------------


def calculate_revenue(wh: Warehouse, day: date) -> int:
    with wh.transaction():
        wh.execute("DELETE FROM daily_revenue WHERE revenue_date = ?", [day])
        wh.run("daily/calculate_revenue", [day])
        balances = wh.scalar("SELECT COUNT(*) FROM balances WHERE balance_date = ?", [day])
        revenue = wh.scalar("SELECT COUNT(*) FROM daily_revenue WHERE revenue_date = ?", [day])
        # An inner join silently drops balances without a matching rate. Silent
        # loss of revenue is the worst failure for billing, so fail loudly
        # (the raise rolls the whole transaction back).
        if revenue != balances:
            raise RevenueIncomplete(
                f"{day}: {balances} balances but {revenue} revenue rows: "
                "a bank has no rate in force (check seeds/bank_rates.csv)"
            )
    return revenue


# --- partner payouts ---------------------------------------------------------


class UnknownPartner(Exception):
    """A customer is linked to a partner_id that is not in seeds/partners.csv."""


def calculate_partner_payouts(wh: Warehouse, day: date) -> int:
    with wh.transaction():
        wh.execute("DELETE FROM partner_payouts_daily WHERE payout_date = ?", [day])
        wh.run("daily/calculate_partner_payouts", [day])
        unknown = wh.query(
            "SELECT DISTINCT d.partner_id FROM daily_revenue d "
            "LEFT JOIN partners p ON p.partner_id = d.partner_id "
            "WHERE d.revenue_date = ? AND d.partner_id IS NOT NULL AND p.partner_id IS NULL "
            "ORDER BY 1",
            [day],
        )
        if unknown:  # raising rolls the transaction back
            raise UnknownPartner(
                f"{day}: unknown partner_id(s) {', '.join(r[0] for r in unknown)}: "
                "add them to seeds/partners.csv or ask the bank to correct the file"
            )
    return wh.scalar("SELECT COUNT(*) FROM partner_payouts_daily WHERE payout_date = ?", [day])
