# Runbook

What to do when something in the billing pipeline fails. Commands assume the
local stack (`docker compose up -d`); on real AWS drop `--endpoint-url`.

```bash
S3="aws --endpoint-url http://localhost:4566 s3"
BUCKET=s3://savehub-bank-files
```

## Where things are

| What | Where |
|---|---|
| Files waiting for validation | `$BUCKET/incoming/` (should be empty within seconds) |
| Accepted files | `$BUCKET/validated/YYYY-MM-DD/bank_<bank>_YYYY-MM-DD.csv` |
| Rejected files + reason | `$BUCKET/rejected/<file>` and `<file>.error.txt` |
| Exported documents | `$BUCKET/invoices/YYYY-MM/`, `$BUCKET/statements/YYYY-MM/` |
| Lambda logs | `docker compose logs localstack \| grep savehub-validate` (CloudWatch on AWS) |
| DAG logs | Airflow UI, click the task square, tab **Logs** |
| Warehouse | `data/warehouse.duckdb` (open read-only while no task runs) |

## 1. A bank file was rejected

Symptom: `rejected/` is not empty, and `daily_revenue.load` for that date fails
with `MissingBankFiles: ... no validated file for bank(s) beta`.

1. Read the reason:
   ```bash
   $S3 ls $BUCKET/rejected/
   $S3 cp $BUCKET/rejected/bank_beta_2026-10-05.csv.error.txt -
   ```
2. **Do not edit the file yourself.** The bank is the source of the data: ask
   the bank to send a corrected file with the same name. (Exception, agreed in
   writing with the bank: an obvious format issue such as a decimal comma.)
3. The corrected file goes to `incoming/` (the bank uploads it, or
   `$S3 cp bank_beta_2026-10-05.csv $BUCKET/incoming/`). The Lambda validates it
   within seconds and removes the old entry from `rejected/`.
4. The `load` task retries by itself (2 retries, 15 min apart). Not waiting:
   in the UI, open the failed `load` task and click **Clear** (with
   downstream). Clearing re-runs `load`, `calculate_revenue` and
   `calculate_partner_payouts` for that date only.

Rejection reasons and what they usually mean:

| Reason in .error.txt | Typical cause |
|---|---|
| `missing column(s)` / `unexpected column(s)` | Bank changed its export. Ask which change is intended |
| `balance_eur is negative` | Overdraft or sign error at the bank. Never "fix" by dropping the minus |
| `uses a decimal comma` | Export with German locale |
| `date ... does not match file name date` | Wrong day exported, or file renamed by hand |
| `customer ... already appears on line` | Duplicate export rows. Would double revenue |
| `unknown bank` | New bank not yet in `seeds/bank_rates.csv`, or a typo in the file name |

## 2. A bank file did not arrive

Symptom: same `MissingBankFiles` error, nothing in `rejected/` for that bank and
date. The task retries twice, then fails.

1. Check `incoming/`: a file stuck there means the Lambda did not run
   (`docker compose logs localstack`). Re-upload the file to trigger it again.
2. Otherwise contact the bank. Once the file is in `validated/`, clear the
   failed `load` task.

Days are all-or-nothing: if one bank is missing, nothing is loaded for that day,
so revenue is never computed from a partial day.

## 3. `calculate_revenue` fails: RevenueIncomplete

`... balances but ... revenue rows: a bank has no rate in force`.
A bank delivers files but `seeds/bank_rates.csv` has no rate valid on that
date. Add a row with the agreed rate and its `valid_from`, commit, clear the
task. Never edit an existing rate row: a rate change is a new row with a later
`valid_from`, so past months stay reproducible.

## 4. `calculate_partner_payouts` fails: UnknownPartner

A customer row carries a `partner_id` that is not in `seeds/partners.csv`.
Either a new partner contract (add the partner, commit, clear the task) or a
bank data error (ask the bank for a corrected file, then see section 1).

## 5. Backfill or re-run a period

Every task deletes and re-inserts its own date (or month), so re-running is
always safe and gives identical numbers.

```bash
# business dates 2026-10-01 .. 2026-10-31 (Airflow 3 bounds backfills by run
# time: the run for 10-31 happens on 11-01)
docker compose exec airflow airflow backfill create --dag-id daily_revenue \
    --from-date 2026-10-01 --to-date 2026-11-01
# add --reprocess-behavior completed to re-run days that already succeeded
```

The DAG must be unpaused for backfill runs to start. Only one backfill per DAG
can run at a time.

## 6. `monthly_invoices` fails

**create_invoices / create_partner_statements: MonthIncomplete.**
Some day has no revenue from some bank, or missing payouts. The message lists
the days. Fix those days in `daily_revenue` (sections 1 to 5); the monthly
tasks retry by themselves (3 x 30 min) or can be cleared.

**reconcile: ReconciliationFailed.** The invoices or statements do not match
the daily data. Nothing has been exported. Retries are off on purpose: this
does not fix itself.

1. Read the problem list in the task log (`check_name: subject expected X actual Y`).
2. Most common cause: a day was re-run after the invoices were created (late
   corrected file). Re-run `create_invoices` and `create_partner_statements`
   (clear them) so the documents reflect the corrected data; reconcile runs again.
3. If the documents are fresh and still do not match: stop. This is a bug in
   the pipeline. Do not export by hand. Investigate with the SQL in
   `sql/monthly/reconcile_*.sql` against `data/warehouse.duckdb`.

**reconcile: ReconciliationNotWritten.** The reconciliation SQL is still a
stub, see `docs/reconciliation_task.md`.

**export fails.** S3 permissions or connectivity. Export overwrites the
month's files and deletes leftovers, so just clear and re-run it.

## 7. A month was already sent and the data changes later

Out of scope for now (see README, "Next steps"): the pipeline has no
correction documents. Re-running the month would overwrite the CSVs of an
invoice the bank may already have received. Today: do not re-export a sent
month; raise it with finance, who issue a manual credit note.

## Health checks

```bash
docker compose ps                                   # all healthy / up
curl -s localhost:8080/api/v2/monitor/health        # Airflow scheduler + DB
curl -s localhost:4566/_localstack/init/ready       # bucket + Lambda deployed
uv run --env-file .env pytest -m integration        # S3 -> Lambda end to end
```

LocalStack (free plan) loses all S3 data on restart. Regenerate with
`uv run --env-file .env generate.py --month YYYY-MM`: the generator is
deterministic, so the data is identical.
