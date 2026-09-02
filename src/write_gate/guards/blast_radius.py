"""Blast-radius guard: estimate affected rows for UPDATE/DELETE before execute."""

from __future__ import annotations

from write_gate.adapters.base import BACKEND_DUCKDB, count_sql, sqlglot_dialect
from write_gate.decision import RULE_BLAST, RISK_CRITICAL, GuardResult
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
    estimated = _estimate_rows(conn, table, parsed.where, dialect=dialect)
    evidence = {
        "estimated_rows": estimated,
        "max_rows": limit,
        "operation": parsed.operation,
        "table": table,
    }
    if estimated is None:
        return GuardResult.pass_(NAME, evidence={**evidence, "skipped": True})
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


def _estimate_rows(conn, table: str, where, dialect: str = BACKEND_DUCKDB) -> int | None:
    # Table name comes from the parser identifier, not raw user interpolation of extra SQL.
    if not table.isidentifier():
        return None
    predicate = where_sql(where, dialect=sqlglot_dialect(dialect))
    sql = count_sql(table, predicate, backend=dialect)
    try:
        row = conn.execute(sql).fetchone()
    except Exception:
        return None
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None
