"""Settings read from the environment (filled from .env, never hard-coded secrets).

AWS endpoint and credentials are NOT read here on purpose: boto3 reads
AWS_ENDPOINT_URL / AWS_ACCESS_KEY_ID / ... itself, so the same code talks to
LocalStack or real AWS depending only on the environment.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEEDS_DIR = Path(os.environ.get("SAVEHUB_SEEDS_DIR", PROJECT_ROOT / "seeds"))


def s3_bucket() -> str:
    return os.environ.get("S3_BUCKET", "savehub-bank-files")


INCOMING_PREFIX = "incoming/"
