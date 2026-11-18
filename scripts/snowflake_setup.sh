#!/bin/bash
# Create a key pair for the Snowflake service user (once) and print the setup
# SQL with the public key filled in, ready to paste into a Snowsight worksheet.
# The private key stays in secrets/ (git-ignored) and never leaves this machine.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p secrets && chmod 700 secrets
if [ ! -f secrets/snowflake_key.p8 ]; then
  openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -inform PEM -out secrets/snowflake_key.p8
  chmod 600 secrets/snowflake_key.p8
fi
PUB=$(openssl rsa -in secrets/snowflake_key.p8 -pubout 2>/dev/null | grep -v -- '-----' | tr -d '\n')
sed "s|__PUBLIC_KEY__|$PUB|" infra/snowflake_setup.sql > secrets/snowflake_setup.sql
echo "Ready: secrets/snowflake_setup.sql (paste it into a Snowsight worksheet, run all)"
