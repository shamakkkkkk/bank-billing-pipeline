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


def connect() -> Warehouse:
    kind = os.environ.get("WAREHOUSE", "duckdb")
    if kind == "duckdb":
        default = config.PROJECT_ROOT / "data" / "warehouse.duckdb"
        return DuckDBWarehouse(os.environ.get("DUCKDB_PATH", default))
    if kind == "snowflake":
        raise NotImplementedError("Snowflake arrives in step 6")
    raise ValueError(f"WAREHOUSE must be duckdb or snowflake, got {kind!r}")
