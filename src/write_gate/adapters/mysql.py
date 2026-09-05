"""MySQL adapter. User SQL must not be executed from here; use WriteGate."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from write_gate.adapters.base import BACKEND_MYSQL, count_sql as _count_sql

DIALECT = "mysql"
BACKEND = BACKEND_MYSQL

ORDERS_DDL = """
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount DOUBLE NOT NULL,
    dt DATE NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(64),
    status VARCHAR(64) NOT NULL
)
"""


def count_sql(table: str, predicate: str | None) -> str:
    """Blast-radius estimate: COUNT(*) of the matching predicate (not EXPLAIN)."""
    return _count_sql(table, predicate, backend=BACKEND_MYSQL)


def explain_sql(table: str, predicate: str | None) -> str:
    """Optional EXPLAIN form. Blast-radius uses count_sql() instead."""
    return f"EXPLAIN {count_sql(table, predicate)}"


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    """Parse mysql:// or mysql+pymysql:// into driver connect kwargs."""
    parsed = urlparse(dsn.strip())
    database = unquote(parsed.path.lstrip("/")) if parsed.path else ""
    kwargs: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password is not None else "",
        "database": database or None,
    }
    return {k: v for k, v in kwargs.items() if v is not None}


def _connect_raw(dsn: str, **kwargs: Any) -> Any:
    """Open a driver connection. Tests may monkeypatch this."""
    timeout = kwargs.pop("connect_timeout", 3)
    params = _parse_mysql_dsn(dsn)
    params.update(kwargs)
    params.setdefault("connect_timeout", timeout)
    try:
        import pymysql

        return pymysql.connect(**params)
    except ImportError:
        pass
    try:
        import mysql.connector

        # mysql.connector uses connection_timeout, not connect_timeout.
        if "connect_timeout" in params:
            params["connection_timeout"] = params.pop("connect_timeout")
        return mysql.connector.connect(**params)
    except ImportError as exc:
        raise ImportError(
            "MySQL support requires pymysql. "
            "Install with: pip install 'write-gate[mysql]'"
        ) from exc


class MySQLConnection:
    """DuckDB-like execute/fetchone surface over pymysql or mysql.connector."""

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
        raise RuntimeError("MySQL connection does not support execute/cursor")

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if callable(close):
            close()

    def commit(self) -> None:
        commit = getattr(self._raw, "commit", None)
        if callable(commit):
            commit()


def connect(dsn: str, *, read_only: bool = False) -> MySQLConnection:
    del read_only
    return MySQLConnection(_connect_raw(dsn))


def execute_user_sql(conn: Any, sql: str):
    """Run already-gated SQL. Called only by write_gate.wrapper.WriteGate."""
    return conn.execute(sql)
