"""The Lambda's S3 side (moving files), against moto's in-memory fake S3."""

import boto3
import pytest
import validate as v
from moto import mock_aws

BUCKET = "test-bucket"
GOOD = b"date,bank,customer_id,partner_id,balance_eur\n2026-10-01,alpha,C0001,,10.00\n"
BAD = b"date,bank,customer_id,partner_id,balance_eur\n2026-10-01,alpha,C0001,,-10.00\n"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)  # moto, not LocalStack
    monkeypatch.setenv("KNOWN_BANKS", "alpha,beta,gamma")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield client


def s3_event(key):
    """The shape of the event S3 sends to Lambda (only the fields we use)."""
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def upload_and_trigger(s3, name, body):
    key = f"incoming/{name}"
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)
    return v.lambda_handler(s3_event(key), None, s3=s3)["results"][0]


def keys(s3):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def test_good_file_goes_to_validated_by_date(s3):
    result = upload_and_trigger(s3, "bank_alpha_2026-10-01.csv", GOOD)
    assert result["status"] == "validated"
    assert keys(s3) == ["validated/2026-10-01/bank_alpha_2026-10-01.csv"]


def test_bad_file_goes_to_rejected_with_reason(s3):
    result = upload_and_trigger(s3, "bank_alpha_2026-10-01.csv", BAD)
    assert result["status"] == "rejected"
    assert keys(s3) == [
        "rejected/bank_alpha_2026-10-01.csv",
        "rejected/bank_alpha_2026-10-01.csv.error.txt",
    ]
    report = s3.get_object(Bucket=BUCKET, Key="rejected/bank_alpha_2026-10-01.csv.error.txt")
    text = report["Body"].read().decode()
    assert "line 2: balance_eur is negative (-10.00)" in text
    # the original bytes are kept untouched, for investigation
    original = s3.get_object(Bucket=BUCKET, Key="rejected/bank_alpha_2026-10-01.csv")
    assert original["Body"].read() == BAD


def test_corrected_reupload_clears_rejected(s3):
    upload_and_trigger(s3, "bank_alpha_2026-10-01.csv", BAD)
    upload_and_trigger(s3, "bank_alpha_2026-10-01.csv", GOOD)
    assert keys(s3) == ["validated/2026-10-01/bank_alpha_2026-10-01.csv"]


def test_duplicate_event_is_harmless(s3):
    """S3 delivers events at least once: the second delivery finds nothing to do."""
    upload_and_trigger(s3, "bank_alpha_2026-10-01.csv", GOOD)
    event = s3_event("incoming/bank_alpha_2026-10-01.csv")
    assert v.lambda_handler(event, None, s3=s3)["results"][0]["status"] == "skipped"
    assert keys(s3) == ["validated/2026-10-01/bank_alpha_2026-10-01.csv"]


def test_url_encoded_key(s3):
    """Keys arrive URL-encoded in events ("my file.csv" -> "my+file.csv")."""
    s3.put_object(Bucket=BUCKET, Key="incoming/my file.csv", Body=GOOD)
    result = v.lambda_handler(s3_event("incoming/my+file.csv"), None, s3=s3)["results"][0]
    assert result["status"] == "rejected"  # bad name, but it was found and handled
    assert "rejected/my file.csv" in keys(s3)
