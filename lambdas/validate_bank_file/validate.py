"""AWS Lambda: validate a bank balance file the moment it lands in S3.

Trigger:  s3:ObjectCreated on incoming/*.csv
Valid  -> validated/<YYYY-MM-DD>/<file>
Invalid-> rejected/<file>  +  rejected/<file>.error.txt  (human-readable reasons)

Only the standard library and boto3 (which the Lambda runtime ships), so the
deployment package is this single file zipped.

`validate()` is a pure function (file name + bytes in, list of problems out):
all business rules are tested without AWS. `lambda_handler()` only moves files.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import UTC, date, datetime
from urllib.parse import unquote_plus

EXPECTED_COLUMNS = {"date", "bank", "customer_id", "partner_id", "balance_eur"}
FILENAME_RE = re.compile(r"^bank_(?P<bank>[a-z0-9]+)_(?P<date>\d{4}-\d{2}-\d{2})\.csv$")
# Plain non-negative euros with at most 2 decimals. A strict regex instead of
# Decimal(): Decimal also accepts "NaN", "Infinity", "1e5" and " 12 ".
AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
MAX_ERRORS = 20  # enough to see the pattern, short enough to read

INCOMING, VALIDATED, REJECTED = "incoming/", "validated/", "rejected/"


# --- business rules (pure) ----------------------------------------------------


def validate(filename: str, content: bytes, known_banks: set[str] | None = None) -> list[str]:
    """Return a list of problems; an empty list means the file is valid."""
    m = FILENAME_RE.match(filename)
    if not m:
        return [f"file name {filename!r} does not match bank_<bank>_<YYYY-MM-DD>.csv"]
    bank = m["bank"]
    try:
        file_date = date.fromisoformat(m["date"]).isoformat()
    except ValueError:
        return [f"file name contains an invalid date {m['date']!r}"]
    if known_banks and bank not in known_banks:
        return [f"unknown bank {bank!r}, expected one of: {', '.join(sorted(known_banks))}"]

    try:
        text = content.decode("utf-8-sig")  # -sig: tolerate the BOM Excel adds
    except UnicodeDecodeError:
        return ["file is not valid UTF-8 text"]

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        return ["file is empty"]
    columns = set(reader.fieldnames)
    if columns != EXPECTED_COLUMNS or len(reader.fieldnames) != len(columns):
        problems = []
        if missing := EXPECTED_COLUMNS - columns:
            problems.append(f"missing column(s): {', '.join(sorted(missing))}")
        if extra := columns - EXPECTED_COLUMNS:
            problems.append(f"unexpected column(s): {', '.join(sorted(extra))}")
        return problems or ["duplicate column names in header"]

    errors: list[str] = []
    first_seen: dict[str, int] = {}
    rows = 0
    for line, row in enumerate(reader, start=2):  # line 1 is the header
        rows += 1
        errors += _check_row(line, row, bank, file_date, first_seen)
        if len(errors) >= MAX_ERRORS:
            errors = errors[:MAX_ERRORS] + [f"stopped after {MAX_ERRORS} problems"]
            break
    if rows == 0:
        errors.append("file has a header but no rows")
    return errors


def _check_row(
    line: int, row: dict, bank: str, file_date: str, first_seen: dict[str, int]
) -> list[str]:
    if None in row:
        return [f"line {line}: more values than columns"]
    if None in row.values():
        return [f"line {line}: fewer values than columns"]

    errors = []
    if row["date"] != file_date:
        errors.append(
            f"line {line}: date {row['date']!r} does not match file name date {file_date}"
        )
    if row["bank"] != bank:
        errors.append(f"line {line}: bank {row['bank']!r} does not match file name bank {bank!r}")

    customer = row["customer_id"].strip()
    if not customer:
        errors.append(f"line {line}: customer_id is empty")
    elif customer in first_seen:
        # A duplicate would silently double this customer's revenue.
        errors.append(
            f"line {line}: customer {customer} already appears on line {first_seen[customer]}"
        )
    else:
        first_seen[customer] = line

    amount = row["balance_eur"]
    if amount == "":
        errors.append(f"line {line}: balance_eur is empty")
    elif amount.startswith("-") and AMOUNT_RE.match(amount[1:]):
        errors.append(f"line {line}: balance_eur is negative ({amount})")
    elif re.fullmatch(r"\d+,\d{1,2}", amount):
        errors.append(f"line {line}: balance_eur uses a decimal comma ({amount}), expected a dot")
    elif not AMOUNT_RE.match(amount):
        errors.append(f"line {line}: balance_eur {amount!r} is not an amount like 1234.56")
    return errors


def error_report(key: str, errors: list[str], now: datetime) -> str:
    lines = [
        f"File:        {key}",
        f"Rejected at: {now.isoformat(timespec='seconds')}",
        f"Problems ({len(errors)}):",
        *(f"  - {e}" for e in errors),
        "",
        f"Fix the file and upload it again to {INCOMING} with the same name.",
    ]
    return "\n".join(lines) + "\n"


# --- AWS glue -------------------------------------------------------------------


def lambda_handler(event: dict, context: object, s3=None) -> dict:
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    known_banks = {b for b in os.environ.get("KNOWN_BANKS", "").split(",") if b}
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # keys arrive URL-encoded
        results.append(process_file(s3, bucket, key, known_banks))
    return {"results": results}


def process_file(s3, bucket: str, key: str, known_banks: set[str]) -> dict:
    filename = key.rsplit("/", 1)[-1]
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        # S3 delivers events at least once: a duplicate event for a file we
        # already moved is normal, not an error.
        return _log({"file": key, "status": "skipped", "reason": "already processed"})

    errors = validate(filename, body, known_banks)
    if errors:
        dest = REJECTED + filename
        # Reason first, then the file: a file in rejected/ always has its reason.
        s3.put_object(
            Bucket=bucket,
            Key=dest + ".error.txt",
            Body=error_report(key, errors, datetime.now(UTC)).encode(),
            ContentType="text/plain; charset=utf-8",
        )
        s3.copy_object(Bucket=bucket, Key=dest, CopySource={"Bucket": bucket, "Key": key})
        status = "rejected"
    else:
        file_date = FILENAME_RE.match(filename)["date"]
        dest = f"{VALIDATED}{file_date}/{filename}"
        s3.copy_object(Bucket=bucket, Key=dest, CopySource={"Bucket": bucket, "Key": key})
        # A corrected re-upload closes the old problem: rejected/ = open problems only.
        for stale in (REJECTED + filename, REJECTED + filename + ".error.txt"):
            s3.delete_object(Bucket=bucket, Key=stale)
        status = "validated"

    # Copy first, delete last: if we crash in between, the file is in both
    # places and the retry simply does the same thing again (idempotent).
    s3.delete_object(Bucket=bucket, Key=key)
    return _log({"file": key, "status": status, "destination": dest, "errors": errors})


def _log(result: dict) -> dict:
    print(json.dumps(result))  # one JSON line per file -> searchable in CloudWatch Logs
    return result
