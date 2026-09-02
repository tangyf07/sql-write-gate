"""The only SQL write tool. All INSERT/UPDATE/DELETE go through WriteGate.execute."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from write_gate.adapters.base import (
    BACKEND_DUCKDB,
    BACKEND_POSTGRES,
    resolve_target,
)
from write_gate.audit import append_audit
from write_gate.catalog import Catalog, load_catalog
from write_gate.config import Policy, load_policy
from write_gate.decision import ACTION_ALLOW, Decision, Evidence
from write_gate.engine import evaluate
from write_gate.paths import AUDIT_PATH, CATALOG_PATH, DB_PATH, POLICY_PATH

__all__ = ["WriteGate", "Evidence", "Decision"]


class WriteGate:
    """Deterministic pre-write gate wrapping DuckDB or PostgreSQL."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        catalog_path: Path | None = None,
        catalog: Catalog | None = None,
        conn: Any | None = None,
        policy_path: Path | None = None,
        policy: Policy | None = None,
        audit_path: Path | None = None,
        agent: str = "cli",
        database: str | None = None,
        database_url: str | None = None,
    ) -> None:
        backend, target = resolve_target(
            database=database,
            database_url=database_url,
            db_path=db_path,
        )
        self.backend = backend
        self.database = target
        self.db_path = Path(target) if backend == BACKEND_DUCKDB else DB_PATH
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        self.catalog = catalog or load_catalog(self.catalog_path)
        self.policy_path = Path(policy_path) if policy_path else POLICY_PATH
        self.policy = policy or load_policy(self.policy_path)
        self.audit_path = Path(audit_path) if audit_path else AUDIT_PATH
        self.agent = agent
        self._conn = conn
        self._owns_conn = conn is None

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> Any:
        if self.backend == BACKEND_POSTGRES:
            from write_gate.adapters.postgres import connect as pg_connect

            return pg_connect(self.database)
        from write_gate.adapters.duckdb import connect as duck_connect

        return duck_connect(self.db_path)

    def _conn_if_available(self) -> Any | None:
        if self._conn is not None:
            return self._conn
        if self.backend == BACKEND_DUCKDB and self.db_path.exists():
            return self.conn
        return None

    def _conn_for_execute(self) -> Any | None:
        """Prefer a live connection for blast-radius; AST guards still run if connect fails."""
        if self._conn is not None:
            return self._conn
        try:
            return self.conn
        except Exception:
            return None

    def _evaluate(self, sql: str, *, use_conn: bool) -> Decision:
        if use_conn:
            conn = self._conn_for_execute()
        else:
            conn = self._conn_if_available()
        return evaluate(
            sql,
            self.catalog,
            policy=self.policy,
            conn=conn,
            dialect=self.backend,
        )

    def _audit(self, decision: Decision) -> None:
        append_audit(
            decision,
            agent=self.agent,
            environment=self.policy.environment,
            path=self.audit_path,
        )

    def check(self, sql: str) -> Decision:
        decision = self._evaluate(sql, use_conn=False)
        self._audit(decision)
        return decision

    def execute(self, sql: str) -> tuple[Decision, Any]:
        """Gate then (only if ALLOW) run SQL via the single adapter write path."""
        decision = self._evaluate(sql, use_conn=True)
        self._audit(decision)
        if decision.action != ACTION_ALLOW:
            return decision, None
        result = self._execute_user_sql(sql)
        return decision, result

    def _execute_user_sql(self, sql: str):
        if self.backend == BACKEND_POSTGRES:
            from write_gate.adapters.postgres import execute_user_sql as exec_sql
        else:
            from write_gate.adapters.duckdb import execute_user_sql as exec_sql
        return exec_sql(self.conn, sql)

    def close(self) -> None:
        if self._owns_conn and self._conn is not None:
            close = getattr(self._conn, "close", None)
            if callable(close):
                close()
            self._conn = None

    def __enter__(self) -> "WriteGate":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
