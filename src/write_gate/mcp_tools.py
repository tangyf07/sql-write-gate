"""MCP tool functions: check-only, importable without the MCP SDK.

query_sql / write_sql both call WriteGate.check (never execute). Honor
database= / DATABASE_URL the same way as the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from write_gate.wrapper import WriteGate


def _as_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value)


def _gate(
    *,
    database: str | None = None,
    db_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    agent: str = "mcp",
) -> WriteGate:
    return WriteGate(
        database=database,
        db_path=_as_path(db_path),
        catalog_path=_as_path(catalog_path),
        policy_path=_as_path(policy_path),
        agent=agent or "mcp",
    )


def decision_payload(decision: Any) -> dict[str, Any]:
    """JSON-serializable gate result for MCP tools / python -c demos."""
    return {
        "action": decision.action,
        "rule_id": decision.rule_id,
        "reason": decision.reason,
        "operation": decision.operation,
        "table": decision.table,
        "risk": decision.risk,
    }


def _check(
    sql: str,
    *,
    database: str | None = None,
    db_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    agent: str = "mcp",
) -> dict[str, Any]:
    with _gate(
        database=database,
        db_path=db_path,
        catalog_path=catalog_path,
        policy_path=policy_path,
        agent=agent,
    ) as gate:
        decision = gate.check(sql)
    return decision_payload(decision)


def query_sql(
    sql: str,
    *,
    database: str | None = None,
    db_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    agent: str = "mcp",
) -> dict[str, Any]:
    """Check a SELECT (or any SQL) through WriteGate.check. Never executes.

    Non-read statements still run the gate; they are not executed.
    """
    return _check(
        sql,
        database=database,
        db_path=db_path,
        catalog_path=catalog_path,
        policy_path=policy_path,
        agent=agent,
    )


def write_sql(
    sql: str,
    *,
    database: str | None = None,
    db_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    agent: str = "mcp",
) -> dict[str, Any]:
    """Check INSERT/UPDATE/DELETE/DDL through WriteGate.check. Never executes.

    Does not call WriteGate.execute even when the decision is ALLOW.
    """
    return _check(
        sql,
        database=database,
        db_path=db_path,
        catalog_path=catalog_path,
        policy_path=policy_path,
        agent=agent,
    )
