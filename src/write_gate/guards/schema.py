"""Schema guard: parse errors, unknown table/column, type mismatch, unsupported SQL."""

from __future__ import annotations

from sqlglot import exp

from write_gate.catalog import TableSpec
from write_gate.decision import RULE_SCHEMA, GuardResult
from write_gate.parser import literal_value, type_ok

NAME = "schema"


def check_schema(ctx) -> GuardResult:
    parsed = ctx.parsed
    if parsed.error:
        return GuardResult.block(
            NAME,
            parsed.error_rule or RULE_SCHEMA,
            parsed.error,
            risk="medium",
        )

    stmt = parsed.statement
    if stmt is None:
        return GuardResult.block(NAME, RULE_SCHEMA, "SQL 无法解析", risk="medium")

    operation = parsed.operation
    if operation == "select":
        return GuardResult.pass_(NAME)

    if operation == "ddl":
        # Destructive/environment guards own DROP/ALTER/TRUNCATE/CREATE.
        return GuardResult.pass_(NAME)

    if operation not in {"insert", "update", "delete"}:
        return GuardResult.block(
            NAME,
            RULE_SCHEMA,
            f"不支持的写语句类型 {type(stmt).__name__}，仅允许 INSERT/UPDATE/DELETE",
            risk="medium",
        )

    table_name = parsed.table
    if not table_name:
        return GuardResult.block(NAME, RULE_SCHEMA, "无法从 SQL 中解析目标表", risk="medium")

    spec = ctx.catalog.table(table_name)
    if spec is None:
        return GuardResult.block(
            NAME,
            RULE_SCHEMA,
            f"未知表 {table_name}，不在目录中",
            risk="medium",
            evidence={"table": table_name},
        )

    if isinstance(stmt, exp.Insert):
        return _check_insert(parsed, spec)
    if isinstance(stmt, exp.Update):
        return _check_update(parsed, spec)
    return GuardResult.pass_(NAME)


def _check_insert(parsed, spec: TableSpec) -> GuardResult:
    cols = list(parsed.write_columns)
    if not cols:
        cols = list(spec.columns.keys())
        parsed.write_columns = cols
        parsed.columns = cols
    if any(c == "" for c in cols):
        return GuardResult.block(NAME, RULE_SCHEMA, "INSERT 列名无法解析", risk="medium")

    rows = parsed.insert_rows
    if rows is None:
        return GuardResult.block(
            NAME,
            RULE_SCHEMA,
            "仅支持 INSERT ... VALUES (...); INSERT ... SELECT 未开放",
            risk="medium",
        )

    for row in rows:
        if len(row) != len(cols):
            return GuardResult.block(
                NAME,
                RULE_SCHEMA,
                f"INSERT 列数 {len(cols)} 与值个数 {len(row)} 不一致",
                risk="medium",
            )
        assignments = dict(zip(cols, row))
        failed = _columns_and_types(spec, cols, assignments)
        if failed:
            return failed
    return GuardResult.pass_(NAME)


def _check_update(parsed, spec: TableSpec) -> GuardResult:
    assignments = parsed.assignments
    cols = list(assignments.keys())
    if not cols:
        return GuardResult.block(NAME, RULE_SCHEMA, "UPDATE 未解析到 SET 列", risk="medium")
    failed = _columns_and_types(spec, cols, assignments)
    if failed:
        return failed
    return GuardResult.pass_(NAME)


def _columns_and_types(
    spec: TableSpec,
    write_cols: list[str],
    assignments: dict,
) -> GuardResult | None:
    unknown = [c for c in write_cols if c not in spec.columns]
    if unknown:
        return GuardResult.block(
            NAME,
            RULE_SCHEMA,
            f"未知列 {unknown}，表 {spec.name} 的列为 {sorted(spec.columns)}",
            risk="medium",
            evidence={"unknown_columns": unknown, "table": spec.name},
        )

    for col in write_cols:
        node = assignments.get(col)
        expected = spec.columns[col]
        if node is not None and not type_ok(expected, node):
            got = literal_value(node)
            return GuardResult.block(
                NAME,
                RULE_SCHEMA,
                f"列 {col} 类型不匹配: 期望 {expected}，实际值 {got!r}",
                risk="medium",
                evidence={"column": col, "expected": expected, "value": got},
            )

    skip = spec.pii_columns | spec.restricted_columns
    not_allowed = [c for c in write_cols if c not in spec.allowed_write_columns and c not in skip]
    if not_allowed:
        return GuardResult.block(
            NAME,
            RULE_SCHEMA,
            f"列 {not_allowed} 不在允许写入列表 {sorted(spec.allowed_write_columns)}",
            risk="medium",
            evidence={"not_allowed": not_allowed},
        )
    return None
