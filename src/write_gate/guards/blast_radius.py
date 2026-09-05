"""Blast-radius guard: estimate affected rows for UPDATE/DELETE before execute.

Fail closed when a connection is available but the row estimate cannot be
computed (never skip/allow on estimate errors). Without a connection, skip
so AST-only check paths can still surface environment / destructive rules.
"""

from __future__ import annotations

from write_gate.adapters.base import BACKEND_DUCKDB, count_sql, sqlglot_dialect
from write_gate.decision import RULE_BLAST, RULE_BLAST_UNKNOWN, RISK_CRITICAL, GuardResult
from write_gate.parser import where_sql

NAME = "blast_radius"


def check_blast_radius(ctx) -> GuardResult:
    parsed = ctx.parsed
    if parsed.statement is None or parsed.error:
        return GuardResult.pass_(NAME)
    if parsed.operation not in {"update", "delete"}:
        return GuardResult.pass_(NAME)

    table = parsed.table
    if not table:
        return GuardResult.pass_(NAME)

    limit = ctx.policy.row_limit(parsed.operation)
    if limit is None:
        return GuardResult.pass_(NAME)

    conn = ctx.conn
    if conn is None:
        return GuardResult.pass_(
            NAME,
            evidence={"skipped": True, "reason": "no connection to estimate rows"},
        )

    dialect = getattr(ctx, "dialect", BACKEND_DUCKDB)
    alias = getattr(parsed, "table_alias", None)
    estimated, err = _estimate_rows(
        conn,
        table,
        parsed.where,
        dialect=dialect,
        alias=alias,
    )
    evidence = {
        "estimated_rows": estimated,
        "max_rows": limit,
        "operation": parsed.operation,
        "table": table,
        "table_alias": alias,
    }
    if err:
        evidence["reason"] = err
    if estimated is None:
        return GuardResult.block(
            NAME,
            RULE_BLAST_UNKNOWN,
            (
                f"Cannot estimate blast radius for {parsed.operation.upper()} on {table}"
                f"{f' ({err})' if err else ''}; blocked (fail closed)"
            ),
            risk=RISK_CRITICAL,
            evidence=evidence,
        )
    if estimated > limit:
        return GuardResult.block(
            NAME,
            RULE_BLAST,
            (
                f"{parsed.operation.upper()} on {table} would affect {estimated} rows, "
                f"exceeding max {limit}"
            ),
            risk=RISK_CRITICAL,
            evidence=evidence,
        )
    return GuardResult.pass_(NAME, evidence=evidence)


def _estimate_rows(
    conn,
    table: str,
    where,
    dialect: str = BACKEND_DUCKDB,
    alias: str | None = None,
) -> tuple[int | None, str | None]:
    # Table name comes from the parser identifier, not raw user interpolation of extra SQL.
    if not table.isidentifier():
        return None, "table name is not a safe identifier"
    if alias is not None and not str(alias).isidentifier():
        return None, "table alias is not a safe identifier"
    predicate = where_sql(where, dialect=sqlglot_dialect(dialect))
    sql = count_sql(table, predicate, backend=dialect, alias=alias)
    try:
        row = conn.execute(sql).fetchone()
    except Exception as exc:
        return None, f"count query failed: {exc}"
    if not row:
        return 0, None
    try:
        return int(row[0]), None
    except (TypeError, ValueError):
        return None, "count result was not an integer"
