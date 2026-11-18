# SaveHub bank billing pipeline

Work in progress. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for scope and build order.

## Getting started

Requirements: Docker, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # set LOCALSTACK_AUTH_TOKEN
docker compose up -d          # Airflow UI on http://localhost:8080
uv sync                       # local Python env for the generator and tests

# simulate October: 3 banks x 31 days, 2 of them deliberately broken
uv run --env-file .env generate.py --month 2026-10 --bad-files 2

uv run pytest
```
