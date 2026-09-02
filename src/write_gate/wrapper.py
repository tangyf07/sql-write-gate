"""The only SQL write tool. All INSERT/UPDATE/DELETE go through WriteGate.execute."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from write_gate.catalog import Catalog, load_catalog
from write_gate.db import connect, execute_user_sql
from write_gate.paths import CATALOG_PATH, DB_PATH
from write_gate.policy import Evidence, evaluate

__all__ = ["WriteGate", "Evidence"]


class WriteGate:
    """Deterministic pre-write gate wrapping a DuckDB file warehouse."""

    def __init__(
        self,
        db_path: Path | None = None,
        catalog_path: Path | None = None,
        catalog: Catalog | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        self.catalog = catalog or load_catalog(self.catalog_path)
        self._conn = conn
        self._owns_conn = conn is None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = connect(self.db_path)
        return self._conn

    def check(self, sql: str) -> Evidence:
        return evaluate(sql, self.catalog)

    def execute(self, sql: str) -> tuple[Evidence, Any]:
        """Gate then (only if allowed) run SQL via the single DuckDB write path."""
        evidence = self.check(sql)
        if not evidence.allowed:
            return evidence, None
        result = execute_user_sql(self.conn, sql)
        return evidence, result

    def close(self) -> None:
        if self._owns_conn and self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "WriteGate":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
