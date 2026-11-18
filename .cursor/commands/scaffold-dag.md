# Scaffold a DAG

Create a new Airflow DAG for this project. Ask me for anything below that I did
not give you: DAG id, schedule (cron, Berlin time), what one run processes
(a day? a month?), and the steps.

Produce, following `.cursor/rules/`:

1. `src/savehub/<module>.py`: one plain function per step, signature
   `(wh: Warehouse, ..., <date or month>)`. Each step deletes what it writes for
   its period, then inserts, inside `wh.transaction()`. Raise a named exception
   with an actionable message when a check fails.
2. `sql/<module>/<step>.sql` for each SQL step, with the header comment.
   New tables go to `sql/schema.sql`.
3. `dags/<dag_id>.py`: thin TaskFlow DAG (see `dags/daily_revenue.py` as the
   template): `CronDataIntervalTimetable`, `catchup=False`, `pool="warehouse"`,
   retries, `doc_md=__doc__` with a backfill example, imports inside tasks.
4. `tests/test_<module>.py`: uses the `wh`, `s3`, `customers` fixtures. At least:
   the happy path with an independent `Decimal` expectation, re-running the same
   period gives identical rows, and each raised exception.
5. Add the DAG id to `scripts/check_dags.py` and a section to `docs/runbook.md`.

Then run `uv run ruff check . && uv run ruff format . && uv run pytest` and fix
until green. Do not commit.
