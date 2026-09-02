"""Policy engine: run guards, reduce to a single Decision.

any BLOCK → BLOCK; else any APPROVAL → REQUIRE_APPROVAL; else ALLOW.
Guard order prefers specific dangerous-SQL rules over environment policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from write_gate.catalog import Catalog
from write_gate.config import Policy, production_policy
from write_gate.decision import (
    ACTION_ALLOW,
    ACTION_APPROVAL,
    ACTION_BLOCK,
    RISK_LOW,
    RULE_OK,
    VERDICT_APPROVAL,
    VERDICT_BLOCK,
    Decision,
    GuardResult,
)
from write_gate.guards import (
    check_blast_radius,
    check_destructive,
    check_environment,
    check_freshness,
    check_pii,
    check_schema,
)
from write_gate.parser import ParsedSQL, parse

GuardFn = Callable[["Context"], GuardResult]

# Destructive first so DELETE without WHERE reports delete_without_where
# even when environment also blocks DELETE.
GUARDS: list[GuardFn] = [
    check_destructive,
    check_schema,
    check_pii,
    check_freshness,
    check_blast_radius,
    check_environment,
]


@dataclass
class Context:
    sql: str
    parsed: ParsedSQL
    catalog: Catalog
    policy: Policy
    conn: Any | None = None
    guard_results: list[GuardResult] = field(default_factory=list)


def evaluate(
    sql: str,
    catalog: Catalog,
    policy: Policy | None = None,
    conn: Any | None = None,
) -> Decision:
    parsed = parse(sql)
    ctx = Context(
        sql=sql,
        parsed=parsed,
        catalog=catalog,
        policy=policy or production_policy(),
        conn=conn,
    )
    results = [guard(ctx) for guard in GUARDS]
    ctx.guard_results = results
    return reduce(ctx, results)


def reduce(ctx: Context, results: list[GuardResult]) -> Decision:
    parsed = ctx.parsed
    estimated = _first_estimated(results)
    evidence_acc: dict[str, Any] = {}
    for result in results:
        if result.evidence:
            evidence_acc[result.name] = result.evidence

    blocks = [r for r in results if r.verdict == VERDICT_BLOCK]
    approvals = [r for r in results if r.verdict == VERDICT_APPROVAL]

    if blocks:
        chosen = blocks[0]
        return Decision(
            action=ACTION_BLOCK,
            risk=chosen.risk,
            rule_id=chosen.rule_id or RULE_OK,
            reason=chosen.reason,
            evidence={**evidence_acc, **chosen.evidence},
            sql=ctx.sql,
            operation=parsed.operation,
            table=parsed.table,
            estimated_rows=estimated if estimated is not None else chosen.evidence.get("estimated_rows"),
        )
    if approvals:
        chosen = approvals[0]
        return Decision(
            action=ACTION_APPROVAL,
            risk=chosen.risk,
            rule_id=chosen.rule_id or RULE_OK,
            reason=chosen.reason,
            evidence={**evidence_acc, **chosen.evidence},
            sql=ctx.sql,
            operation=parsed.operation,
            table=parsed.table,
            estimated_rows=estimated if estimated is not None else chosen.evidence.get("estimated_rows"),
        )
    return Decision(
        action=ACTION_ALLOW,
        risk=RISK_LOW,
        rule_id=RULE_OK,
        reason=_allow_reason(parsed),
        evidence=evidence_acc,
        sql=ctx.sql,
        operation=parsed.operation,
        table=parsed.table,
        estimated_rows=estimated,
    )


def _first_estimated(results: list[GuardResult]) -> int | None:
    for result in results:
        value = result.evidence.get("estimated_rows") if result.evidence else None
        if isinstance(value, int):
            return value
    return None


def _allow_reason(parsed: ParsedSQL) -> str:
    if parsed.operation == "select":
        return "只读 SELECT，绕过写库门禁"
    table = parsed.table or "?"
    if parsed.operation == "insert":
        return f"写入通过门禁: 表 {table}，列 {parsed.write_columns}，分区未过期且不含 PII"
    if parsed.operation == "update":
        return f"UPDATE 通过门禁: 表 {table}，列 {parsed.write_columns}"
    if parsed.operation == "delete":
        return f"DELETE 通过门禁: 表 {table}"
    return f"{parsed.operation.upper()} 通过门禁: 表 {table}"
