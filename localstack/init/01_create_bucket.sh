#!/bin/bash
# Runs inside LocalStack once it is ready. On real AWS the bucket would be
# created once by hand or by infrastructure code, not by the pipeline.
set -euo pipefail
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-central-1
export AWS_ENDPOINT_URL=http://localhost:4566
BUCKET=${S3_BUCKET:-savehub-bank-files}
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null \
  || aws s3api create-bucket --bucket "$BUCKET" \
       --create-bucket-configuration LocationConstraint=eu-central-1 >/dev/null
echo "bucket $BUCKET ready"
