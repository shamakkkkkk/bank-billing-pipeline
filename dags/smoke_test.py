"""Proves the wiring: Airflow runs, imports our package, and reaches S3 (LocalStack).

Trigger it manually in the UI. It will be removed once the real DAGs exist.
"""

import pendulum
from airflow.sdk import dag, task


@dag(
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["smoke"],
)
def smoke_test():
    @task
    def list_incoming() -> int:
        import boto3

        from savehub import config

        s3 = boto3.client("s3")
        resp = s3.list_objects_v2(Bucket=config.s3_bucket(), Prefix=config.INCOMING_PREFIX)
        keys = [obj["Key"] for obj in resp.get("Contents", [])]
        print(f"{len(keys)} objects in s3://{config.s3_bucket()}/{config.INCOMING_PREFIX}")
        for key in keys[:10]:
            print("  ", key)
        return len(keys)

    list_incoming()


smoke_test()
