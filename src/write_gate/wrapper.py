"""The only SQL write tool. All INSERT/UPDATE/DELETE go through WriteGate.execute."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from write_gate.adapters.duckdb import connect, execute_user_sql
from write_gate.audit import append_audit
from write_gate.catalog import Catalog, load_catalog
from write_gate.config import Policy, load_policy
from write_gate.decision import ACTION_ALLOW, Decision, Evidence
from write_gate.engine import evaluate
from write_gate.paths import AUDIT_PATH, CATALOG_PATH, DB_PATH, POLICY_PATH

__all__ = ["WriteGate", "Evidence", "Decision"]


class WriteGate:
    """Deterministic pre-write gate wrapping a DuckDB file warehouse."""

    def __init__(
        self,
        db_path: Path | None = None,
        catalog_path: Path | None = None,
        catalog: Catalog | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
        policy_path: Path | None = None,
        policy: Policy | None = None,
        audit_path: Path | None = None,
        agent: str = "cli",
    ) -> None:
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        self.catalog = catalog or load_catalog(self.catalog_path)
        self.policy_path = Path(policy_path) if policy_path else POLICY_PATH
        self.policy = policy or load_policy(self.policy_path)
        self.audit_path = Path(audit_path) if audit_path else AUDIT_PATH
        self.agent = agent
        self._conn = conn
        self._owns_conn = conn is None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = connect(self.db_path)
        return self._conn

    def _conn_if_available(self) -> duckdb.DuckDBPyConnection | None:
        if self._conn is not None:
            return self._conn
        if self.db_path.exists():
            return self.conn
        return None

    def _evaluate(self, sql: str, *, use_conn: bool) -> Decision:
        conn = self.conn if use_conn else self._conn_if_available()
        return evaluate(sql, self.catalog, policy=self.policy, conn=conn)

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
        """Gate then (only if ALLOW) run SQL via the single DuckDB write path."""
        decision = self._evaluate(sql, use_conn=True)
        self._audit(decision)
        if decision.action != ACTION_ALLOW:
            return decision, None
        result = execute_user_sql(self.conn, sql)
        return decision, result

    def close(self) -> None:
        if self._owns_conn and self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "WriteGate":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
