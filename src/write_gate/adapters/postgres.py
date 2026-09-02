"""PostgreSQL adapter. User SQL must not be executed from here; use WriteGate."""

from __future__ import annotations

from typing import Any

from write_gate.adapters.base import BACKEND_POSTGRES, count_sql as _count_sql

DIALECT = "postgres"
BACKEND = BACKEND_POSTGRES

ORDERS_DDL = """
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    dt DATE NOT NULL,
    email VARCHAR,
    phone VARCHAR,
    status VARCHAR NOT NULL
)
"""


def count_sql(table: str, predicate: str | None) -> str:
    """Blast-radius estimate: COUNT(*) of the matching predicate (not EXPLAIN)."""
    return _count_sql(table, predicate, backend=BACKEND_POSTGRES)


def explain_sql(table: str, predicate: str | None) -> str:
    """Optional EXPLAIN form. Blast-radius uses count_sql() instead."""
    return f"EXPLAIN {count_sql(table, predicate)}"


def _connect_raw(dsn: str, **kwargs: Any) -> Any:
    """Open a driver connection. Tests may monkeypatch this."""
    timeout = kwargs.pop("connect_timeout", 3)
    try:
        import psycopg

        return psycopg.connect(dsn, autocommit=True, connect_timeout=timeout, **kwargs)
    except ImportError:
        pass
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=timeout, **kwargs)
        conn.autocommit = True
        return conn
    except ImportError as exc:
        raise ImportError(
            "PostgreSQL support requires psycopg. "
            "Install with: pip install 'write-gate[postgres]'"
        ) from exc


class PostgresConnection:
    """DuckDB-like execute/fetchone surface over psycopg or psycopg2."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        try:
            self._raw.autocommit = True
        except Exception:
            pass

    def execute(self, sql: str):
        cursor_fn = getattr(self._raw, "cursor", None)
        if callable(cursor_fn):
            cur = cursor_fn()
            cur.execute(sql)
            return cur
        execute = getattr(self._raw, "execute", None)
        if callable(execute):
            return execute(sql)
        raise RuntimeError("PostgreSQL connection does not support execute/cursor")

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if callable(close):
            close()

    def commit(self) -> None:
        commit = getattr(self._raw, "commit", None)
        if callable(commit):
            commit()


def connect(dsn: str, *, read_only: bool = False) -> PostgresConnection:
    del read_only
    return PostgresConnection(_connect_raw(dsn))


def execute_user_sql(conn: Any, sql: str):
    """Run already-gated SQL. Called only by write_gate.wrapper.WriteGate."""
    return conn.execute(sql)
