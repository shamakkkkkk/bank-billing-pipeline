# Airflow image + the few libraries our DAGs need (boto3 is already included).
FROM apache/airflow:3.4.0-python3.12
COPY requirements-airflow.txt /requirements-airflow.txt
RUN pip install --no-cache-dir -r /requirements-airflow.txt
