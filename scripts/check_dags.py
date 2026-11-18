"""Fail if any DAG in dags/ does not import. Runs inside the Airflow image:

docker compose run --rm --no-deps airflow python /opt/airflow/scripts/check_dags.py
"""

from airflow.dag_processing.dagbag import DagBag

bag = DagBag("/opt/airflow/dags")
if bag.import_errors:
    for path, error in bag.import_errors.items():
        print(f"IMPORT ERROR {path}:\n{error}")
    raise SystemExit(1)
expected = {"daily_revenue", "monthly_invoices"}
if missing := expected - set(bag.dag_ids):
    raise SystemExit(f"missing DAGs: {sorted(missing)}")
print("DAGs OK:", sorted(bag.dag_ids))
