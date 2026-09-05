"""The only SQL write tool. All INSERT/UPDATE/DELETE go through WriteGate.execute."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from write_gate.adapters.base import (
    BACKEND_DUCKDB,
    BACKEND_MYSQL,
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    resolve_target,
)
from write_gate.approvals import (
    ApprovalError,
    enqueue_approval,
    get_approval,
    mark_approved,
    mark_rejected,
)
from write_gate.audit import append_audit
from write_gate.catalog import Catalog, load_catalog
from write_gate.config import Policy, load_policy
from write_gate.decision import ACTION_ALLOW, ACTION_APPROVAL, Decision, Evidence
from write_gate.engine import evaluate
from write_gate.paths import (
    default_approvals_path,
    default_audit_path,
    default_catalog_path,
    default_db_path,
    default_policy_path,
)

__all__ = ["WriteGate", "Evidence", "Decision"]


class WriteGate:
    """Deterministic pre-write gate wrapping DuckDB, PostgreSQL, MySQL, or SQLite."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        catalog_path: Path | None = None,
        catalog: Catalog | None = None,
        conn: Any | None = None,
        policy_path: Path | None = None,
        policy: Policy | None = None,
        audit_path: Path | None = None,
        approvals_path: Path | str | None = None,
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
        self.db_path = Path(target) if backend == BACKEND_DUCKDB else default_db_path()
        self.catalog_path = Path(catalog_path) if catalog_path else default_catalog_path()
        self.catalog = catalog or load_catalog(self.catalog_path)
        self.policy_path = Path(policy_path) if policy_path else default_policy_path()
        self.policy = policy or load_policy(self.policy_path)
        self.audit_path = Path(audit_path) if audit_path else default_audit_path()
        self.approvals_path = (
            Path(approvals_path) if approvals_path else default_approvals_path()
        )
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
        if self.backend == BACKEND_MYSQL:
            from write_gate.adapters.mysql import connect as mysql_connect

            return mysql_connect(self.database)
        if self.backend == BACKEND_SQLITE:
            from write_gate.adapters.sqlite import connect as sqlite_connect

            return sqlite_connect(self.database)
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

    def _evaluate(
        self, sql: str, *, use_conn: bool, human_approved: bool = False
    ) -> Decision:
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
            human_approved=human_approved,
        )

    def _audit(
        self,
        decision: Decision,
        *,
        executed: bool | None = None,
        execution_outcome: str | None = None,
    ) -> None:
        append_audit(
            decision,
            agent=self.agent,
            environment=self.policy.environment,
            path=self.audit_path,
            database=self.database,
            executed=executed,
            execution_outcome=execution_outcome,
        )

    def check(self, sql: str) -> Decision:
        decision = self._evaluate(sql, use_conn=False)
        self._audit(decision)
        return decision

    def execute(self, sql: str) -> tuple[Decision, Any]:
        """Gate then (only if ALLOW) run SQL via the single adapter write path.

        REQUIRE_APPROVAL is enqueued and not executed. BLOCK is not queued
        and not executed. check() stays evaluate-only (no enqueue).
        """
        decision = self._evaluate(sql, use_conn=True)
        if decision.action == ACTION_APPROVAL:
            rec = enqueue_approval(
                sql=sql,
                decision=decision,
                database=self.database,
                db_path=str(self.db_path) if self.backend == BACKEND_DUCKDB else None,
                policy_path=str(self.policy_path) if self.policy_path else None,
                catalog_path=str(self.catalog_path) if self.catalog_path else None,
                path=self.approvals_path,
                backend=self.backend,
                agent=self.agent,
            )
            decision.approval_id = rec.id
            rec.decision = decision.to_dict()
            self._audit(
                decision,
                executed=False,
                execution_outcome="queued",
            )
            return decision, None
        if decision.action != ACTION_ALLOW:
            self._audit(
                decision,
                executed=False,
                execution_outcome="blocked",
            )
            return decision, None
        result = self._execute_user_sql(sql)
        self._audit(
            decision,
            executed=True,
            execution_outcome="executed",
        )
        return decision, result

    def approve(self, approval_id: str) -> tuple[Decision, Any]:
        """Load id, re-run guards with human approval, execute if ALLOW.

        Clears environment 'approval' rules and PII SELECT approval for this
        queued statement. Destructive / PII-write / freshness / blast BLOCK
        still apply. Idempotent: already-approved ids do not double-execute.
        """
        rec = get_approval(approval_id, path=self.approvals_path)
        if rec is None:
            raise ApprovalError(f"approval not found: {approval_id}")
        if rec.status == "approved":
            # Idempotent: do not execute again.
            decision = Decision(
                action=ACTION_ALLOW,
                risk="low",
                rule_id="ok",
                reason=f"approval {rec.id} already approved (idempotent; not re-executed)",
                sql=rec.sql,
                approval_id=rec.id,
                operation=(rec.decision or {}).get("operation"),
                table=(rec.decision or {}).get("table"),
            )
            self._audit(
                decision,
                executed=False,
                execution_outcome="already_approved",
            )
            return decision, None
        if rec.status != "pending":
            raise ApprovalError(f"approval not pending: {approval_id}")
        saved_policy = self.policy
        try:
            self.policy = saved_policy.with_env_approvals_cleared()
            decision = self._evaluate(rec.sql, use_conn=True, human_approved=True)
        finally:
            self.policy = saved_policy
        decision.approval_id = rec.id
        if decision.action != ACTION_ALLOW:
            self._audit(
                decision,
                executed=False,
                execution_outcome="approve_blocked",
            )
            return decision, None
        result = self._execute_user_sql(rec.sql)
        mark_approved(rec.id, path=self.approvals_path)
        self._audit(
            decision,
            executed=True,
            execution_outcome="executed",
        )
        return decision, result

    def reject(self, approval_id: str) -> None:
        """Mark pending id rejected. Does not write."""
        mark_rejected(approval_id, path=self.approvals_path)

    def _execute_user_sql(self, sql: str):
        if self.backend == BACKEND_POSTGRES:
            from write_gate.adapters.postgres import execute_user_sql as exec_sql
        elif self.backend == BACKEND_MYSQL:
            from write_gate.adapters.mysql import execute_user_sql as exec_sql
        elif self.backend == BACKEND_SQLITE:
            from write_gate.adapters.sqlite import execute_user_sql as exec_sql
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
