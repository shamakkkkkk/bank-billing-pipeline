"""daily_revenue: validated bank files -> balances -> daily revenue.

Schedule: 07:00 Berlin, for the PREVIOUS day. Airflow's data interval for the
run at 2026-10-02 07:00 is [10-01 07:00, 10-02 07:00), and its logical date
({{ ds }}) is 2026-10-01: exactly the business date whose files arrived overnight.

Backfill October (one run per business date, safe to repeat):
    airflow backfill create --dag-id daily_revenue \
        --from-date 2026-10-01 --to-date 2026-11-01

(Airflow 3 bounds backfills by run time: the run for 10-31 happens on 11-01.)

The DAG is thin on purpose: all logic lives in savehub.daily and is unit-tested.
"""

from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

BUSINESS_DATE = "{{ ds }}"  # rendered by Airflow per run, e.g. "2026-10-01"


@dag(
    dag_id="daily_revenue",
    schedule=CronDataIntervalTimetable("0 7 * * *", timezone="Europe/Berlin"),
    start_date=pendulum.datetime(2026, 10, 1, tz="Europe/Berlin"),
    catchup=False,  # past dates only on purpose, via backfill
    max_active_runs=3,
    default_args={
        "pool": "warehouse",  # one task at a time touches DuckDB
        # A missing file is usually late, not lost: retry before paging anyone.
        "retries": 2,
        "retry_delay": timedelta(minutes=15),
    },
    tags=["billing", "daily"],
    doc_md=__doc__,
)
def daily_revenue():
    @task
    def prepare() -> None:
        """Create tables if missing; refresh bank rates and partners from seeds."""
        from savehub import daily, warehouse

        with warehouse.connect() as wh:
            daily.prepare(wh)

    @task
    def load(business_date: str) -> dict[str, int]:
        """All banks' validated files for the day -> balances (delete + insert)."""
        import boto3

        from savehub import daily, warehouse

        with warehouse.connect() as wh:
            counts = daily.load_balances(wh, boto3.client("s3"), date.fromisoformat(business_date))
        print(f"{business_date}: loaded rows per bank {counts}")
        return counts  # visible in the XCom tab

    @task
    def calculate_revenue(business_date: str) -> int:
        """balances x bank rate in force -> daily_revenue (delete + insert)."""
        from savehub import daily, warehouse

        with warehouse.connect() as wh:
            rows = daily.calculate_revenue(wh, date.fromisoformat(business_date))
            total = wh.scalar(
                "SELECT SUM(revenue_eur) FROM daily_revenue WHERE revenue_date = ?",
                [business_date],
            )
        print(f"{business_date}: {rows} revenue rows, total {total} EUR (unrounded)")
        return rows

    prepare() >> load(BUSINESS_DATE) >> calculate_revenue(BUSINESS_DATE)


daily_revenue()
