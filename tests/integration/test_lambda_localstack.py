"""End-to-end: upload to the real (LocalStack) S3 and let the deployed Lambda react.

Needs the stack:  docker compose up -d
Run:              uv run --env-file .env pytest -m integration
"""

import time

import boto3
import pytest

from savehub import config

pytestmark = pytest.mark.integration

GOOD = b"date,bank,customer_id,partner_id,balance_eur\n2025-01-15,alpha,C9001,,10.00\n"
BAD = b"date,bank,customer_id,partner_id,balance_eur\n2025-01-15,beta,C9002,,12,50\n"


@pytest.fixture
def s3():
    client = boto3.client("s3")
    yield client
    # clean up only what this test created
    for key in (
        "validated/2025-01-15/bank_alpha_2025-01-15.csv",
        "rejected/bank_beta_2025-01-15.csv",
        "rejected/bank_beta_2025-01-15.csv.error.txt",
    ):
        client.delete_object(Bucket=config.s3_bucket(), Key=key)


def wait_for(s3, key, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return s3.get_object(Bucket=config.s3_bucket(), Key=key)["Body"].read()
        except s3.exceptions.NoSuchKey:
            time.sleep(1)
    pytest.fail(f"{key} did not appear within {timeout}s: is the Lambda deployed?")


def test_upload_is_sorted_by_lambda(s3):
    bucket = config.s3_bucket()
    s3.put_object(Bucket=bucket, Key="incoming/bank_alpha_2025-01-15.csv", Body=GOOD)
    s3.put_object(Bucket=bucket, Key="incoming/bank_beta_2025-01-15.csv", Body=BAD)

    assert wait_for(s3, "validated/2025-01-15/bank_alpha_2025-01-15.csv") == GOOD
    report = wait_for(s3, "rejected/bank_beta_2025-01-15.csv.error.txt").decode()
    assert "more values than columns" in report  # "12,50" splits into two CSV fields

    left = s3.list_objects_v2(Bucket=bucket, Prefix="incoming/bank_").get("Contents", [])
    assert not [o for o in left if "2025-01-15" in o["Key"]], "files must leave incoming/"
