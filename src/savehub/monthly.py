"""Monthly pipeline steps: invoices to banks, statements to partners, the
reconciliation that guards them, and the export to S3.

All monthly SQL reads the month from a one-row temp table `billing_period`
(month_label, period_start, period_end, created_at) instead of positional
parameters: the queries stay readable and the reconciliation SQL needs none.
"""

from __future__ import annotations

import calendar
import csv
import io
from datetime import UTC, date, datetime, timedelta

from savehub import config, daily
from savehub.warehouse import Warehouse, read_sql

RECONCILE_CHECKS = ("monthly/reconcile_invoices", "monthly/reconcile_partner_payouts")


class MonthIncomplete(Exception):
    """Some day of the month has no (or partial) revenue or payouts yet."""


class ReconciliationFailed(Exception):
    """The monthly documents do not match the daily data."""


class ReconciliationNotWritten(Exception):
    """A reconcile_*.sql file is still the TODO stub."""


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-10-31)"""
    year, mon = (int(x) for x in month.split("-"))
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def set_billing_period(wh: Warehouse, month: str) -> None:
    start, end = month_bounds(month)
    created_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    wh.execute(
        "CREATE OR REPLACE TEMPORARY TABLE billing_period AS SELECT "
        "CAST(? AS VARCHAR) AS month_label, CAST(? AS DATE) AS period_start, "
        "CAST(? AS DATE) AS period_end, CAST(? AS TIMESTAMP) AS created_at",
        [month, start, end, created_at],
    )


def check_month_complete(wh: Warehouse, month: str) -> None:
    """Never invoice a month with holes: every day needs revenue from every
    expected bank, and payouts for every referred customer."""
    start, end = month_bounds(month)
    problems = []
    day = start
    while day <= end:
        expected = set(daily.expected_banks(wh, day))
        rows = wh.query(
            "SELECT bank, COUNT(*), COUNT(partner_id) FROM daily_revenue "
            "WHERE revenue_date = ? GROUP BY bank",
            [day],
        )
        present = {r[0] for r in rows}
        if missing := expected - present:
            problems.append(f"{day}: no revenue for {', '.join(sorted(missing))}")
        referred = sum(r[2] for r in rows)
        payouts = wh.scalar(
            "SELECT COUNT(*) FROM partner_payouts_daily WHERE payout_date = ?", [day]
        )
        if referred != payouts:
            problems.append(f"{day}: {referred} referred customer rows but {payouts} payouts")
        day += timedelta(days=1)
    if problems:
        shown = "; ".join(problems[:5]) + (
            f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        )
        raise MonthIncomplete(
            f"{month} is not complete: {shown}. Run/backfill daily_revenue first."
        )


def create_invoices(wh: Warehouse, month: str) -> int:
    check_month_complete(wh, month)
    start, _ = month_bounds(month)
    with wh.transaction():
        set_billing_period(wh, month)
        wh.execute("DELETE FROM invoices WHERE invoice_month = ?", [start])
        wh.run("monthly/create_invoices")
    return wh.scalar("SELECT COUNT(*) FROM invoices WHERE invoice_month = ?", [start])


def create_partner_statements(wh: Warehouse, month: str) -> int:
    check_month_complete(wh, month)
    start, _ = month_bounds(month)
    with wh.transaction():
        set_billing_period(wh, month)
        wh.execute("DELETE FROM partner_statements WHERE statement_month = ?", [start])
        wh.run("monthly/create_partner_statements")
    return wh.scalar("SELECT COUNT(*) FROM partner_statements WHERE statement_month = ?", [start])


def reconciliation_problems(wh: Warehouse, month: str) -> list[tuple]:
    """Run every reconcile_*.sql; each returns one row per problem."""
    set_billing_period(wh, month)
    problems = []
    for name in RECONCILE_CHECKS:
        sql = read_sql(name)
        if "TODO" in sql:
            raise ReconciliationNotWritten(
                f"sql/{name}.sql is still a stub: see docs/reconciliation_task.md"
            )
        problems += wh.query(sql)
    return problems


def reconcile(wh: Warehouse, month: str) -> None:
    problems = reconciliation_problems(wh, month)
    if problems:
        lines = "\n".join(
            f"  {check}: {subject} expected {exp} actual {act}"
            for check, subject, exp, act, *_ in problems
        )
        raise ReconciliationFailed(
            f"{month}: {len(problems)} problem(s), nothing exported:\n{lines}"
        )


# --- export -------------------------------------------------------------------

INVOICE_COLUMNS = [
    "invoice_id", "bank", "period_start", "period_end", "customer_days", "amount_eur", "currency",
]  # fmt: skip
STATEMENT_COLUMNS = [
    "statement_id", "partner_id", "partner_name", "period_start", "period_end",
    "referred_customers", "referred_revenue_eur", "amount_eur", "currency",
]  # fmt: skip


def _csv(columns: list[str], row: tuple) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    w.writerow(row)
    return buf.getvalue().encode()


def _sync_prefix(s3, bucket: str, prefix: str, files: dict[str, bytes]) -> list[str]:
    """Make the S3 prefix contain exactly `files`: overwrite, and delete
    leftovers from an earlier run. Rerunning the export is idempotent."""
    for key, body in files.items():
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/csv")
    existing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    for obj in existing:
        if obj["Key"] not in files:
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
    return sorted(files)


def export(wh: Warehouse, s3, month: str, bucket: str | None = None) -> list[str]:
    bucket = bucket or config.s3_bucket()
    start, _ = month_bounds(month)
    invoices = wh.query(
        f"SELECT {', '.join(INVOICE_COLUMNS)} FROM invoices WHERE invoice_month = ? "
        "ORDER BY invoice_id",
        [start],
    )
    statements = wh.query(
        f"SELECT {', '.join(STATEMENT_COLUMNS)} FROM partner_statements "
        "WHERE statement_month = ? ORDER BY statement_id",
        [start],
    )
    keys = _sync_prefix(
        s3, bucket, f"invoices/{month}/",
        {f"invoices/{month}/{r[0]}.csv": _csv(INVOICE_COLUMNS, r) for r in invoices},
    )  # fmt: skip
    keys += _sync_prefix(
        s3, bucket, f"statements/{month}/",
        {f"statements/{month}/{r[0]}.csv": _csv(STATEMENT_COLUMNS, r) for r in statements},
    )  # fmt: skip
    return keys
