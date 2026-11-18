#!/bin/bash
# Runs inside LocalStack after 01_create_bucket.sh. Points the generic deploy
# script at LocalStack; on real AWS you would run infra/deploy_lambda.sh directly.
set -euo pipefail
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-central-1
export AWS_ENDPOINT_URL=http://localhost:4566
MAX_CONCURRENCY=5 /opt/savehub/infra/deploy_lambda.sh  # protects the 5 GB Docker limit
