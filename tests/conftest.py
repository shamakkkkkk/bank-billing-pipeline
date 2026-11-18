"""Fixtures shared by all tests: DuckDB in memory, fake S3 (moto), customers."""

import boto3
import pytest
from bankdata import BUCKET, SEED
from moto import mock_aws

from savehub import daily
from savehub import generator as g
from savehub.warehouse import DuckDBWarehouse


@pytest.fixture
def wh():
    with DuckDBWarehouse(":memory:") as w:
        daily.prepare(w)
        yield w


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield client


@pytest.fixture(scope="module")
def customers():
    return g.make_customers(g.load_banks(), g.load_partner_ids(), SEED)
