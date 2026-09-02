"""Dangerous SQL guard: unbounded DELETE/UPDATE, DROP, TRUNCATE, ALTER."""

from __future__ import annotations

from sqlglot import exp

from write_gate.decision import (
    RULE_ALTER_TABLE,
    RULE_DELETE_NO_WHERE,
    RULE_DROP_TABLE,
    RULE_TRUNCATE,
    RULE_UPDATE_NO_WHERE,
    GuardResult,
)

NAME = "destructive"


def check_destructive(ctx) -> GuardResult:
    parsed = ctx.parsed
    stmt = parsed.statement
    if stmt is None:
        return GuardResult.pass_(NAME)

    if isinstance(stmt, exp.Delete) and not parsed.has_where:
        table = parsed.table or "?"
        return GuardResult.block(
            NAME,
            RULE_DELETE_NO_WHERE,
            f"DELETE without a WHERE clause is forbidden (full-table delete on {table})",
            evidence={"table": table, "has_where": False},
        )

    if isinstance(stmt, exp.Update) and not parsed.has_where:
        table = parsed.table or "?"
        return GuardResult.block(
            NAME,
            RULE_UPDATE_NO_WHERE,
            f"UPDATE without a WHERE clause is forbidden (full-table update on {table})",
            evidence={"table": table, "has_where": False},
        )

    if isinstance(stmt, exp.Drop):
        kind = (stmt.args.get("kind") or "TABLE")
        kind_s = str(kind).upper()
        table = parsed.table or ident_fallback(stmt)
        if kind_s in {"TABLE", "VIEW", ""} or kind is None:
            return GuardResult.block(
                NAME,
                RULE_DROP_TABLE,
                f"DROP {kind_s or 'TABLE'} {table} is forbidden",
                evidence={"table": table, "kind": kind_s},
            )
        return GuardResult.block(
            NAME,
            RULE_DROP_TABLE,
            f"DROP {kind_s} is forbidden",
            evidence={"kind": kind_s},
        )

    if isinstance(stmt, exp.TruncateTable) or type(stmt).__name__ == "TruncateTable":
        table = parsed.table or "?"
        return GuardResult.block(
            NAME,
            RULE_TRUNCATE,
            f"TRUNCATE TABLE {table} is forbidden",
            evidence={"table": table},
        )

    if isinstance(stmt, exp.Alter) or type(stmt).__name__ in {"Alter", "AlterTable"}:
        table = parsed.table or "?"
        return GuardResult.block(
            NAME,
            RULE_ALTER_TABLE,
            f"ALTER TABLE {table} is forbidden",
            evidence={"table": table},
        )

    return GuardResult.pass_(NAME)


def ident_fallback(stmt) -> str:
    this = getattr(stmt, "this", None)
    name = getattr(this, "name", None) if this is not None else None
    return str(name).lower() if name else "?"
