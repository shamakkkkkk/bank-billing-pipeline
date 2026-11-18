# SaveHub bank billing pipeline

Work in progress. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for scope and build order.

## Getting started

Requirements: Docker (5 GB memory), [uv](https://docs.astral.sh/uv/), a free
LocalStack "Hobby" auth token.

```bash
cp .env.example .env          # set LOCALSTACK_AUTH_TOKEN
docker compose up -d          # Airflow UI on http://localhost:8080
                              # LocalStack creates the bucket and deploys the Lambda
uv sync

# simulate October: 3 banks x 31 days, 2 of them deliberately broken
uv run --env-file .env generate.py --month 2026-10 --bad-files 2

uv run pytest                                      # unit tests, no docker needed
uv run --env-file .env pytest -m integration       # end-to-end against LocalStack
```

## How a file flows (so far)

```
generate.py ──> s3://…/incoming/bank_alpha_2026-10-01.csv
                     │  S3 event (only incoming/*.csv)
                     ▼
               Lambda savehub-validate-bank-file
                 ├─ valid   ──> validated/2026-10-01/bank_alpha_2026-10-01.csv
                 └─ invalid ──> rejected/bank_alpha_2026-10-01.csv
                                rejected/bank_alpha_2026-10-01.csv.error.txt
```

Read why a file was rejected:

```bash
aws --endpoint-url http://localhost:4566 s3 ls s3://savehub-bank-files/rejected/
aws --endpoint-url http://localhost:4566 s3 cp s3://savehub-bank-files/rejected/<file>.error.txt -
```

LocalStack (free plan) keeps no data across restarts: after `docker compose down`
regenerate the files. The generator is deterministic, so the data is identical.
