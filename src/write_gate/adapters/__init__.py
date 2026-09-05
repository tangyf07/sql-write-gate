"""Database adapters. DuckDB is default; Postgres/MySQL are selected from a URL."""

from write_gate.adapters.base import (
    BACKEND_DUCKDB,
    BACKEND_MYSQL,
    BACKEND_POSTGRES,
    count_sql,
    detect_backend,
    is_mysql_url,
    is_postgres_url,
    resolve_target,
    sqlglot_dialect,
)
from write_gate.adapters.duckdb import ORDERS_DDL, connect, execute_user_sql

__all__ = [
    "BACKEND_DUCKDB",
    "BACKEND_MYSQL",
    "BACKEND_POSTGRES",
    "ORDERS_DDL",
    "connect",
    "count_sql",
    "detect_backend",
    "execute_user_sql",
    "is_mysql_url",
    "is_postgres_url",
    "resolve_target",
    "sqlglot_dialect",
]
