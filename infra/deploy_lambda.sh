#!/bin/bash
# Deploy the validation Lambda and wire the S3 trigger. Safe to run again (idempotent).
#
# LocalStack: run automatically by localstack/init/ready.d on `docker compose up`.
# Real AWS:   same script, with real credentials and no AWS_ENDPOINT_URL.
#             (The AWS CLI reads AWS_ENDPOINT_URL itself, like boto3 does.)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUCKET=${S3_BUCKET:-savehub-bank-files}
FUNCTION=savehub-validate-bank-file
ROLE=savehub-validate-bank-file-role
# Single source of truth for banks: the same seed file the warehouse uses.
KNOWN_BANKS=$(tail -n +2 "$ROOT/seeds/bank_rates.csv" | cut -d, -f1 | sort -u | paste -sd, -)

# 1. Package: one file, no dependencies.
ZIP=$(mktemp -d)/function.zip
(cd "$ROOT/lambdas/validate_bank_file" && python3 -m zipfile -c "$ZIP" validate.py)

# 2. Execution role: the identity the function runs as. Least privilege.
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document "file://$ROOT/infra/lambda-trust-policy.json" >/dev/null
  NEW_ROLE=1
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name savehub-validate \
  --policy-document "$(sed "s/__BUCKET__/$BUCKET/g" "$ROOT/infra/lambda-policy.json")"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
# Real AWS needs a few seconds before a brand-new role can be assumed.
[ -n "${NEW_ROLE:-}" ] && [ -z "${AWS_ENDPOINT_URL:-}" ] && sleep 10

# 3. Function: create, or update code + config if it exists.
ENV="{\"Variables\":{\"KNOWN_BANKS\":\"$KNOWN_BANKS\"}}"
if aws lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" --zip-file "fileb://$ZIP" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION"
  aws lambda update-function-configuration --function-name "$FUNCTION" \
    --environment "$ENV" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION"
else
  aws lambda create-function --function-name "$FUNCTION" \
    --runtime python3.12 --handler validate.lambda_handler \
    --role "$ROLE_ARN" --zip-file "fileb://$ZIP" \
    --timeout 30 --memory-size 256 --environment "$ENV" >/dev/null
  aws lambda wait function-active-v2 --function-name "$FUNCTION"
fi
FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION" \
  --query Configuration.FunctionArn --output text)

# Optional cap on parallel executions. LocalStack runs each one as a container
# (~65 MB): without a cap, uploading a month started 12 at once. Requests over
# the cap are not lost: async invocations are queued and retried.
# Left unset on real AWS: new accounts have a total limit of 10 and may not reserve.
if [ -n "${MAX_CONCURRENCY:-}" ]; then
  aws lambda put-function-concurrency --function-name "$FUNCTION" \
    --reserved-concurrent-executions "$MAX_CONCURRENCY" >/dev/null
fi

# 4. Allow S3 (only this bucket) to invoke the function.
aws lambda add-permission --function-name "$FUNCTION" --statement-id s3-invoke \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn "arn:aws:s3:::$BUCKET" >/dev/null 2>&1 || true  # exists already

# 5. Trigger ONLY on incoming/*.csv. The function writes to validated/ and
#    rejected/ in the same bucket: without the prefix filter it would trigger
#    itself in an endless loop.
aws s3api put-bucket-notification-configuration --bucket "$BUCKET" \
  --notification-configuration "{
    \"LambdaFunctionConfigurations\": [{
      \"LambdaFunctionArn\": \"$FUNCTION_ARN\",
      \"Events\": [\"s3:ObjectCreated:*\"],
      \"Filter\": {\"Key\": {\"FilterRules\": [
        {\"Name\": \"prefix\", \"Value\": \"incoming/\"},
        {\"Name\": \"suffix\", \"Value\": \".csv\"}
      ]}}
    }]
  }"

echo "lambda $FUNCTION deployed (banks: $KNOWN_BANKS), trigger s3://$BUCKET/incoming/*.csv"
