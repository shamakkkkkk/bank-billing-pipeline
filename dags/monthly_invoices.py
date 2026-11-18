"""monthly_invoices: invoices to banks + statements to partners, reconciled, exported.

Schedule: 09:00 Berlin on the 1st, for the PREVIOUS month: the run on 11-01
has logical date 10-01, so {{ ds[:7] }} = "2026-10".

Order: create_invoices + create_partner_statements -> reconcile -> export.
Nothing leaves the warehouse unless reconcile passes. If the month is not
complete yet (daily_revenue for 10-31 runs at 07:00 the same morning, and
may be retrying), the create tasks fail with MonthIncomplete and retry.

Run October by hand (safe to repeat):
    airflow backfill create --dag-id monthly_invoices \
        --from-date 2026-10-01 --to-date 2026-11-01
"""

from datetime import timedelta

import pendulum
from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

MONTH = "{{ ds[:7] }}"  # "2026-10"


@dag(
    dag_id="monthly_invoices",
    schedule=CronDataIntervalTimetable("0 9 1 * *", timezone="Europe/Berlin"),
    start_date=pendulum.datetime(2026, 10, 1, tz="Europe/Berlin"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "pool": "warehouse",
        "retries": 3,
        "retry_delay": timedelta(minutes=30),
    },
    tags=["billing", "monthly"],
    doc_md=__doc__,
)
def monthly_invoices():
    @task
    def create_invoices(month: str) -> int:
        from savehub import daily, monthly, warehouse

        with warehouse.connect() as wh:
            daily.prepare(wh)  # tables exist even if the daily DAG never ran
            n = monthly.create_invoices(wh, month)
            rows = wh.query(
                "SELECT invoice_id, amount_eur FROM invoices WHERE invoice_month = ? ORDER BY 1",
                [monthly.month_bounds(month)[0]],
            )
        print(f"{month}: {n} invoices {rows}")
        return n

    @task
    def create_partner_statements(month: str) -> int:
        from savehub import daily, monthly, warehouse

        with warehouse.connect() as wh:
            daily.prepare(wh)
            n = monthly.create_partner_statements(wh, month)
            rows = wh.query(
                "SELECT statement_id, amount_eur FROM partner_statements "
                "WHERE statement_month = ? ORDER BY 1",
                [monthly.month_bounds(month)[0]],
            )
        print(f"{month}: {n} statements {rows}")
        return n

    @task(retries=0)  # a mismatch will not fix itself by waiting: page a human
    def reconcile(month: str) -> None:
        from savehub import monthly, warehouse

        with warehouse.connect() as wh:
            monthly.reconcile(wh, month)
        print(f"{month}: reconciled, every amount matches to the cent")

    @task
    def export(month: str) -> list[str]:
        import boto3

        from savehub import monthly, warehouse

        with warehouse.connect() as wh:
            keys = monthly.export(wh, boto3.client("s3"), month)
        print(f"{month}: exported {keys}")
        return keys

    [create_invoices(MONTH), create_partner_statements(MONTH)] >> reconcile(MONTH) >> export(MONTH)


monthly_invoices()
