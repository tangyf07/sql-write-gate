"""DuckDB warehouse helpers. User SQL must not be executed from here; use WriteGate."""

from __future__ import annotations

from pathlib import Path

import duckdb

from write_gate.paths import DB_PATH

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


def connect(db_path: Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def execute_user_sql(conn: duckdb.DuckDBPyConnection, sql: str):
    """Run already-gated SQL. Called only by write_gate.wrapper.WriteGate."""
    return conn.execute(sql)
