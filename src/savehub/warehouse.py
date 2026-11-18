"""The only place that knows which warehouse we talk to.

Everything else (pipeline code, SQL files, DAGs) uses this small interface, so
switching DuckDB -> Snowflake is an env flag (WAREHOUSE=duckdb|snowflake), not
a code change.

SQL conventions that keep the same .sql files valid on both engines:
- parameters are `?` placeholders (qmark style, supported by both drivers)
- no division of money: DuckDB turns DECIMAL / x into DOUBLE (see daily.py)
- portable types only: DATE, VARCHAR, NUMERIC(p,s), TIMESTAMP
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from savehub import config

SQL_DIR = config.PROJECT_ROOT / "sql"


def read_sql(name: str) -> str:
    """read_sql("daily/calculate_revenue") -> contents of sql/daily/calculate_revenue.sql"""
    return (SQL_DIR / f"{name}.sql").read_text()


def split_statements(script: str) -> list[str]:
    """Split a .sql file on ';'. Fine for our own files (no ';' inside strings)."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


class Warehouse(ABC):
    @abstractmethod
    def execute(self, sql: str, params: Sequence = ()) -> None: ...

    @abstractmethod
    def executemany(self, sql: str, rows: Sequence[Sequence]) -> None: ...

    @abstractmethod
    def query(self, sql: str, params: Sequence = ()) -> list[tuple]: ...

    @abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All statements inside commit together or not at all."""

    @abstractmethod
    def close(self) -> None: ...

    # helpers built on the primitives above
    def execute_script(self, script: str) -> None:
        for statement in split_statements(script):
            self.execute(statement)

    def run(self, name: str, params: Sequence = ()) -> None:
        self.execute(read_sql(name), params)

    def scalar(self, sql: str, params: Sequence = ()):
        return self.query(sql, params)[0][0]

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DuckDBWarehouse(Warehouse):
    """Local file (or :memory: in tests). One writer process at a time, which is
    why the Airflow tasks share a pool with a single slot."""

    def __init__(self, path: str | Path = ":memory:"):
        import duckdb

        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))

    def execute(self, sql, params=()):
        self.con.execute(sql, list(params))

    def executemany(self, sql, rows):
        if rows:
            self.con.executemany(sql, [list(r) for r in rows])

    def query(self, sql, params=()):
        return self.con.execute(sql, list(params)).fetchall()

    @contextmanager
    def transaction(self):
        self.con.begin()
        try:
            yield
        except BaseException:
            self.con.rollback()
            raise
        self.con.commit()

    def close(self):
        self.con.close()


class SnowflakeWarehouse(Warehouse):
    """Snowflake, authenticated with a key pair (no password: service users
    cannot use passwords, and the key never leaves the machine running the
    pipeline). Settings come from the environment, see .env.example.

    Snowflake specifics handled here, so the rest of the code does not care:
    - paramstyle="qmark": server-side binding of `?`, same SQL as DuckDB
    - transactions are explicit BEGIN / COMMIT / ROLLBACK
    - DDL inside a transaction would commit it: callers keep CREATE outside
    """

    def __init__(self, schema: str | None = None, **overrides):
        import snowflake.connector

        env = os.environ
        params = {
            "account": env["SNOWFLAKE_ACCOUNT"],
            "user": env["SNOWFLAKE_USER"],
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": env["SNOWFLAKE_PRIVATE_KEY_PATH"],
            "role": env.get("SNOWFLAKE_ROLE", "SAVEHUB_PIPELINE"),
            "warehouse": env.get("SNOWFLAKE_WAREHOUSE", "SAVEHUB_WH"),
            "database": env.get("SNOWFLAKE_DATABASE", "SAVEHUB"),
            "schema": schema or env.get("SNOWFLAKE_SCHEMA", "BILLING"),
            "paramstyle": "qmark",
            "session_parameters": {"QUERY_TAG": "savehub-pipeline"},  # find our queries in history
        }
        params.update(overrides)
        self.con = snowflake.connector.connect(**params)

    def execute(self, sql, params=()):
        with self.con.cursor() as cur:
            cur.execute(sql, list(params) or None)

    def executemany(self, sql, rows):
        if rows:
            with self.con.cursor() as cur:
                cur.executemany(sql, [list(r) for r in rows])  # one array-bound statement

    def query(self, sql, params=()):
        with self.con.cursor() as cur:
            cur.execute(sql, list(params) or None)
            return [tuple(r) for r in cur.fetchall()]

    @contextmanager
    def transaction(self):
        self.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.execute("ROLLBACK")
            raise
        self.execute("COMMIT")

    def close(self):
        self.con.close()


def connect() -> Warehouse:
    kind = os.environ.get("WAREHOUSE", "duckdb")
    if kind == "duckdb":
        default = config.PROJECT_ROOT / "data" / "warehouse.duckdb"
        return DuckDBWarehouse(os.environ.get("DUCKDB_PATH", default))
    if kind == "snowflake":
        return SnowflakeWarehouse()
    raise ValueError(f"WAREHOUSE must be duckdb or snowflake, got {kind!r}")
