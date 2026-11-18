#!/bin/bash
# Runs inside LocalStack once it is ready. On real AWS the bucket would be
# created once by infrastructure code (Terraform/CDK), not by the pipeline.
set -euo pipefail
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-central-1 \
  aws --endpoint-url http://localhost:4566 s3 mb "s3://${S3_BUCKET:-savehub-bank-files}" || true
echo "bucket ${S3_BUCKET:-savehub-bank-files} ready"
