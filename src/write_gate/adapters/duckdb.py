"""DuckDB warehouse helpers. User SQL must not be executed from here; use WriteGate."""

from __future__ import annotations

from pathlib import Path

import duckdb

from write_gate.adapters.base import BACKEND_DUCKDB, count_sql as _count_sql
from write_gate.paths import default_db_path

DIALECT = "duckdb"
BACKEND = BACKEND_DUCKDB

ORDERS_DDL = """
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount DOUBLE NOT NULL,
    dt DATE NOT NULL,
    email VARCHAR,
    phone VARCHAR,
    status VARCHAR NOT NULL
)
"""


def count_sql(table: str, predicate: str | None) -> str:
    """Blast-radius estimate: COUNT(*) of the matching predicate."""
    return _count_sql(table, predicate, backend=BACKEND_DUCKDB)


def connect(db_path: Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def execute_user_sql(conn: duckdb.DuckDBPyConnection, sql: str):
    """Run already-gated SQL. Called only by write_gate.wrapper.WriteGate."""
    return conn.execute(sql)
