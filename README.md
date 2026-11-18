# SaveHub bank billing pipeline

Banks send daily customer balance files. This pipeline validates them, loads
them into a warehouse, computes the platform's daily revenue and partner payouts,
and at month end produces invoices to banks and statements to partners, but
only after checking that every amount reconciles to the cent.

SaveHub is fictional: 3 partner banks, 100 customers, 2 referral partners, one
currency. The engineering problems are real ones: late and broken files,
re-runs, backfills, rounding, and proving that the numbers add up.

> Status: steps 1 to 5 of [PROJECT_BRIEF.md](PROJECT_BRIEF.md) are done. Open:
> the reconciliation SQL ([docs/reconciliation_task.md](docs/reconciliation_task.md)),
> the Snowflake switch, screenshots.

## Why

Portfolio project for a working student role on a corporate services revenue
team (Python, SQL, Airflow, Snowflake, AWS Lambda and S3). Invoicing, partner
compensation and revenue recognition pipelines, in a small and honest version.

## How it works

```
generate.py ──> S3 incoming/bank_alpha_2026-10-01.csv
                   │ S3 event (incoming/*.csv only)
                   ▼
             Lambda: validate ──┬─ ok ──> validated/2026-10-01/bank_alpha_2026-10-01.csv
                                └─ bad ─> rejected/<file> + <file>.error.txt

Airflow  daily_revenue     07:00 Berlin, for the previous day
         prepare ─> load ─> calculate_revenue ─> calculate_partner_payouts
                    balances    daily_revenue        partner_payouts_daily

Airflow  monthly_invoices  09:00 Berlin on the 1st, for the previous month
         create_invoices ─────────┐
                                  ├─> reconcile ─> export ─> S3 invoices/2026-10/*.csv
         create_partner_statements┘                            statements/2026-10/*.csv
```

| Step | Code | What it guarantees |
|---|---|---|
| Generate | [generator.py](src/savehub/generator.py) | Deterministic files; can inject 5 kinds of broken file |
| Validate | [validate.py](lambdas/validate_bank_file/validate.py) | Only well-formed files reach the warehouse; the bank gets a readable reason |
| Load | [daily.py](src/savehub/daily.py) | A day is loaded for all banks or not at all |
| Revenue | [calculate_revenue.sql](sql/daily/calculate_revenue.sql) | `balance × annual rate / 365`, with the rate in force that day |
| Payouts | [calculate_partner_payouts.sql](sql/daily/calculate_partner_payouts.sql) | `revenue × partner share`, for referred customers only |
| Invoices, statements | [sql/monthly/](sql/monthly/) | One document per bank and partner, rounded once |
| Reconcile | [monthly.py](src/savehub/monthly.py) | Nothing is exported unless invoices match revenue and payouts stay below revenue |

## Quick start

Requirements: Docker (5 GB memory), [uv](https://docs.astral.sh/uv/), a free
LocalStack "Hobby" auth token.

```bash
cp .env.example .env          # set LOCALSTACK_AUTH_TOKEN
docker compose up -d          # Airflow on http://localhost:8080, LocalStack on :4566
                              # LocalStack creates the bucket and deploys the Lambda
uv sync

# October: 3 banks x 31 days, 2 files deliberately broken
uv run --env-file .env generate.py --month 2026-10 --bad-files 2

# unpause daily_revenue in the UI, then
docker compose exec airflow airflow backfill create --dag-id daily_revenue \
    --from-date 2026-10-01 --to-date 2026-11-01
```

Unpausing also schedules the regular run for yesterday; it waits for
yesterday's files (`generate.py --date YYYY-MM-DD`). In October, two days fail on
purpose (their file was rejected). Fix them like a real
incident: [docs/runbook.md](docs/runbook.md), section 1.

```bash
uv run pytest                                  # unit tests, no Docker needed
uv run --env-file .env pytest -m integration   # S3 -> Lambda end to end on LocalStack
```

## Design decisions

**Money is never a float.** `Decimal` in Python, `NUMERIC` in SQL. One trap
found on the way: DuckDB computes `DECIMAL / 365` as `DOUBLE`. So SQL never
divides money: the daily rate (`annual / 365`, 12 decimals) is computed once
with `Decimal` when reference data is loaded, and SQL only multiplies, which is
exact.

**Round once, in one place.** Daily revenue and payouts keep full precision
(14 and 18 decimals). The only rounding is `ROUND(SUM(...), 2)` on the monthly
invoice and statement. Rounding 3,100 daily amounts one by one would drift by
cents; the exact sums are stored next to the rounded amounts for audit.

**Every step is idempotent.** Each step deletes what it is about to write (that
day and bank, that day, that month) and inserts, in one transaction. Re-running
a day, or the whole of October, gives identical rows. Verified in tests and on
the running stack (full October reprocess: same 3,100 rows, same sum to 14
decimals). Invoice ids are deterministic (`INV-2026-10-ALPHA`) for the same
reason.

**Validate at the boundary.** The Lambda rejects a broken file within seconds
and writes why, before anything touches the warehouse. Each defect the
generator can produce has a test proving the Lambda catches it. Amounts are
checked with a strict regex, because `Decimal("NaN")` and `Decimal("1e5")`
parse without complaint.

**Fail loudly rather than lose money silently.** An inner join drops rows
without a word. So the pipeline counts: balances without a bank rate, customers
with an unknown partner, a day with a missing bank, or a month with a missing
day all stop the run with a message that says what to do. Retries are on where
waiting helps (a late file) and off where it does not (a reconciliation
mismatch).

**Same code locally and in production.** No endpoint or key in the code:
boto3 and the AWS CLI read `AWS_ENDPOINT_URL` and credentials from the
environment. `.env` points at LocalStack; `.env.aws.example` shows the real-AWS
setup. The Lambda deploy script ([infra/deploy_lambda.sh](infra/deploy_lambda.sh))
is the same for both. Its IAM policy lets the function touch only `incoming/`,
`validated/` and `rejected/`, and the S3 trigger listens to `incoming/` only, or
the function would trigger itself forever.

**Warehouse behind an interface.** [warehouse.py](src/savehub/warehouse.py)
exposes `execute / query / transaction`. DuckDB runs locally and in CI;
Snowflake is an env flag (`WAREHOUSE=snowflake`). SQL files use only syntax both
engines accept. DuckDB allows one writer, so all warehouse tasks share an
Airflow pool with one slot.

**Airflow dates mean business dates.** A `CronDataIntervalTimetable` makes the
run at 07:00 on 2 October carry logical date 1 October, the day whose files
arrived overnight. DAGs are thin; logic lives in `savehub` and is tested
without Airflow.

## Tests

| Level | What | Runs |
|---|---|---|
| Business rules | validation, revenue, payouts, invoices: exact `Decimal` comparisons | `uv run pytest` (DuckDB in memory, moto for S3) |
| Specification | 10 tests the reconciliation SQL must pass | same, skipped until the SQL exists |
| End to end | upload to LocalStack S3, the deployed Lambda sorts the files | `pytest -m integration` |
| DAGs | both DAGs import in the real Airflow image | CI, [scripts/check_dags.py](scripts/check_dags.py) |

CI: [.github/workflows/ci.yml](.github/workflows/ci.yml) runs ruff, the unit
tests and the DAG import check.

## Limitations

- **365-day year**, leap years included.
- **One bank per customer, a fixed customer base**, one currency, one legal entity.
- **Partner shares have no history.** Bank rates do (`valid_from`); partner
  shares would need the same before the first contract change.
- **No corrections after invoicing.** If data changes after an invoice was
  sent, re-running overwrites the document; real billing issues a credit note.
- **Invoice numbers are not sequential.** German invoices need a gapless
  sequence; deterministic ids were chosen for idempotent re-runs.
- **Lambda failures can be lost:** after Lambda's retries the event is gone. A
  dead-letter queue and an alert would fix this.
- **Files are read into memory** (fine for daily bank files, not for gigabytes),
  and loads use row inserts, not `COPY INTO` from a stage.
- LocalStack's free plan keeps no data across restarts; the generator
  re-creates identical data.

## Next steps

Deliberately out of scope: multiple currencies and legal entities, late-data
corrections (adjustment lines and credit notes), a web UI, NetSuite
integration, Kubernetes. Smaller ones: a DLQ for the Lambda, partner share
history, `COPY INTO` for Snowflake loads, alerting on failed tasks.

## Repository map

```
dags/            daily_revenue.py, monthly_invoices.py (thin)
src/savehub/     generator, warehouse interface, daily and monthly steps
sql/             schema.sql, daily/*.sql, monthly/*.sql
lambdas/         validate_bank_file/validate.py (zipped alone)
infra/           deploy_lambda.sh + IAM policies (LocalStack and AWS)
localstack/init/ creates the bucket and deploys the Lambda on startup
seeds/           bank_rates.csv, partners.csv (reference data, with history)
tests/           unit, specification and integration tests
docs/            runbook.md, reconciliation_task.md
.cursor/         project rules + a /scaffold-dag command
```
