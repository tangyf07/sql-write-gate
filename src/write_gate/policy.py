"""Deterministic SQL write-gate policy. No LLM. AST + catalog only."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import sqlglot
from sqlglot import exp

from write_gate.catalog import Catalog, TableSpec

RULE_OK = "ok"
RULE_PII = "pii_column"
RULE_EXPIRED = "expired_partition"
RULE_SCHEMA = "schema_mismatch"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Evidence:
    allowed: bool
    rule_id: str
    message: str
    sql: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(sql: str, message: str) -> Evidence:
    return Evidence(allowed=True, rule_id=RULE_OK, message=message, sql=sql)


def _deny(sql: str, rule_id: str, message: str) -> Evidence:
    return Evidence(allowed=False, rule_id=rule_id, message=message, sql=sql)


def evaluate(sql: str, catalog: Catalog) -> Evidence:
    """Return a gate verdict for a single SQL string."""
    original = sql
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return _deny(original, RULE_SCHEMA, "SQL 为空，无法执行")

    try:
        statements = sqlglot.parse(stripped, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        return _deny(original, RULE_SCHEMA, f"SQL 无法解析: {exc}")

    statements = [s for s in statements if s is not None]
    if not statements:
        return _deny(original, RULE_SCHEMA, "SQL 无法解析为空语句")
    if len(statements) != 1:
        return _deny(
            original,
            RULE_SCHEMA,
            f"一次只允许一条语句，收到 {len(statements)} 条",
        )

    stmt = statements[0]
    if _is_read_only(stmt):
        return _ok(original, "只读 SELECT，绕过写库门禁")

    if isinstance(stmt, exp.Insert):
        return _check_insert(original, stmt, catalog)
    if isinstance(stmt, exp.Update):
        return _check_update(original, stmt, catalog)
    if isinstance(stmt, exp.Delete):
        return _check_delete(original, stmt, catalog)

    return _deny(
        original,
        RULE_SCHEMA,
        f"不支持的写语句类型 {type(stmt).__name__}，仅允许 INSERT/UPDATE/DELETE",
    )


def _is_read_only(stmt: exp.Expression) -> bool:
    names = (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop",
        "AlterTable", "Command", "Copy", "TruncateTable", "Replace",
    )
    write_types = tuple(getattr(exp, n) for n in names if hasattr(exp, n))
    if write_types and isinstance(stmt, write_types):
        return False
    if isinstance(stmt, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return True
    # WITH ... SELECT is still a Select in sqlglot.
    return isinstance(stmt, exp.Query) and not isinstance(stmt, write_types)


def _ident(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, exp.Table):
        return node.name.lower()
    if isinstance(node, exp.Schema):
        return _ident(node.this)
    if isinstance(node, exp.Identifier):
        return node.name.lower()
    name = getattr(node, "name", None)
    return str(name).lower() if name else None


def _literal_value(node: exp.Expression | None) -> Any:
    if node is None:
        return None
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Cast):
        return _literal_value(node.this)
    if isinstance(node, (exp.TsOrDsToDate, exp.Date)):
        return _literal_value(node.this) if node.this else node.sql()
    if isinstance(node, exp.Literal):
        raw = node.this
        if node.is_int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return raw
        if node.is_number:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return raw
        return str(raw)
    # DATE '2026-09-01' sometimes surfaces as anonymous / paren.
    sql = node.sql(dialect="duckdb").strip().strip("'\"")
    return sql


def _expected_kind(col_type: str) -> str:
    t = col_type.upper()
    if t in {"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT"}:
        return "INTEGER"
    if t in {"DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"}:
        return "DOUBLE"
    if t in {"DATE", "TIMESTAMP"}:
        return "DATE"
    return "VARCHAR"


def _type_ok(expected: str, node: exp.Expression | None) -> bool:
    if node is None or isinstance(node, exp.Null):
        return True
    kind = _expected_kind(expected)
    value = _literal_value(node)
    if kind == "INTEGER":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "DOUBLE":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "DATE":
        text = str(value)
        if not _DATE_RE.match(text):
            return False
        try:
            date.fromisoformat(text)
            return True
        except ValueError:
            return False
    return True


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _lookup_table(catalog: Catalog, table_name: str | None, sql: str) -> TableSpec | Evidence:
    if not table_name:
        return _deny(sql, RULE_SCHEMA, "无法从 SQL 中解析目标表")
    spec = catalog.table(table_name)
    if spec is None:
        return _deny(sql, RULE_SCHEMA, f"未知表 {table_name}，不在目录中")
    return spec


def _schema_and_pii(
    sql: str,
    spec: TableSpec,
    write_cols: list[str],
    assignments: dict[str, exp.Expression | None],
) -> Evidence | None:
    """Return a deny Evidence if schema or PII rules fail, else None."""
    unknown = [c for c in write_cols if c not in spec.columns]
    if unknown:
        return _deny(
            sql,
            RULE_SCHEMA,
            f"未知列 {unknown}，表 {spec.name} 的列为 {sorted(spec.columns)}",
        )

    for col in write_cols:
        node = assignments.get(col)
        expected = spec.columns[col]
        if node is not None and not _type_ok(expected, node):
            got = _literal_value(node)
            return _deny(
                sql,
                RULE_SCHEMA,
                f"列 {col} 类型不匹配: 期望 {expected}，实际值 {got!r}",
            )

    pii_hit = [c for c in write_cols if c in spec.pii_columns]
    if pii_hit:
        return _deny(
            sql,
            RULE_PII,
            f"禁止写入 PII 列 {pii_hit}（表 {spec.name} 的 pii_columns={sorted(spec.pii_columns)}）",
        )

    not_allowed = [
        c
        for c in write_cols
        if c not in spec.allowed_write_columns and c not in spec.pii_columns
    ]
    if not_allowed:
        return _deny(
            sql,
            RULE_SCHEMA,
            f"列 {not_allowed} 不在允许写入列表 {sorted(spec.allowed_write_columns)}",
        )
    return None


def _freshness(
    sql: str,
    spec: TableSpec,
    catalog: Catalog,
    partition_dates: list[date | None],
    *,
    missing_ok: bool,
    missing_message: str,
) -> Evidence | None:
    if spec.stale or not spec.writable:
        return _deny(
            sql,
            RULE_EXPIRED,
            f"表 {spec.name} 已标记为 stale/不可写，拒绝全部写入",
        )
    if spec.partition_column:
        present = [d for d in partition_dates if d is not None]
        if not present and not missing_ok:
            return _deny(sql, RULE_SCHEMA, missing_message)
        cutoff = catalog.cutoff_date
        expired = [d for d in present if d < cutoff]
        if expired:
            worst = min(expired)
            return _deny(
                sql,
                RULE_EXPIRED,
                (
                    f"分区 {spec.partition_column}={worst.isoformat()} 早于新鲜度截止日期 "
                    f"{cutoff.isoformat()}（as_of={catalog.as_of_date.isoformat()}, "
                    f"freshness_days={catalog.freshness_days}），拒绝写入"
                ),
            )
    return None


def _insert_columns(stmt: exp.Insert, spec: TableSpec) -> list[str] | Evidence:
    target = stmt.this
    if isinstance(target, exp.Schema) and target.expressions:
        return [_ident(c) or "" for c in target.expressions]
    return list(spec.columns.keys())


def _insert_rows(stmt: exp.Insert) -> list[list[exp.Expression]] | None:
    values = stmt.expression
    if isinstance(values, exp.Values):
        rows: list[list[exp.Expression]] = []
        for tup in values.expressions:
            if isinstance(tup, exp.Tuple):
                rows.append(list(tup.expressions))
            else:
                rows.append([tup])
        return rows
    return None


def _check_insert(sql: str, stmt: exp.Insert, catalog: Catalog) -> Evidence:
    table_name = _ident(stmt.this)
    spec = _lookup_table(catalog, table_name, sql)
    if isinstance(spec, Evidence):
        return spec

    cols = _insert_columns(stmt, spec)
    if isinstance(cols, Evidence):
        return cols
    if any(c == "" for c in cols):
        return _deny(sql, RULE_SCHEMA, "INSERT 列名无法解析")

    rows = _insert_rows(stmt)
    if rows is None:
        return _deny(
            sql,
            RULE_SCHEMA,
            "仅支持 INSERT ... VALUES (...); INSERT ... SELECT 未开放",
        )

    part = spec.partition_column
    partition_dates: list[date | None] = []
    # Schema / PII / types checked per row.
    for row in rows:
        if len(row) != len(cols):
            return _deny(
                sql,
                RULE_SCHEMA,
                f"INSERT 列数 {len(cols)} 与值个数 {len(row)} 不一致",
            )
        assignments = dict(zip(cols, row))
        failed = _schema_and_pii(sql, spec, cols, assignments)
        if failed:
            return failed
        if part:
            if part not in assignments:
                partition_dates.append(None)
            else:
                partition_dates.append(_parse_date(_literal_value(assignments[part])))

    missed = (
        f"INSERT 必须显式写出分区列 {part}" if part else "缺少分区列"
    )
    fresh = _freshness(
        sql,
        spec,
        catalog,
        partition_dates,
        missing_ok=False,
        missing_message=missed,
    )
    if fresh:
        return fresh

    return _ok(sql, f"写入通过门禁: 表 {spec.name}，列 {cols}，分区未过期且不含 PII")


def _update_assignments(stmt: exp.Update) -> dict[str, exp.Expression | None]:
    out: dict[str, exp.Expression | None] = {}
    for item in stmt.expressions:
        if isinstance(item, exp.EQ):
            name = _ident(item.this)
            if name:
                out[name] = item.expression
    return out


def _partition_dates_from_where(where: exp.Expression | None, part: str | None) -> list[date | None]:
    if where is None or not part:
        return []
    dates: list[date | None] = []

    def visit(node: exp.Expression) -> None:
        if isinstance(node, exp.In) and _ident(node.this) == part:
            for item in node.expressions:
                dates.append(_parse_date(_literal_value(item)))
            return
        if isinstance(node, exp.EQ):
            left, right = node.this, node.expression
            if _ident(left) == part:
                dates.append(_parse_date(_literal_value(right)))
            elif _ident(right) == part:
                dates.append(_parse_date(_literal_value(left)))
            return
        for child in node.iter_expressions():
            visit(child)

    visit(where)
    return dates


def _check_update(sql: str, stmt: exp.Update, catalog: Catalog) -> Evidence:
    table_name = _ident(stmt.this)
    spec = _lookup_table(catalog, table_name, sql)
    if isinstance(spec, Evidence):
        return spec

    assignments = _update_assignments(stmt)
    cols = list(assignments.keys())
    if not cols:
        return _deny(sql, RULE_SCHEMA, "UPDATE 未解析到 SET 列")

    failed = _schema_and_pii(sql, spec, cols, assignments)
    if failed:
        return failed

    part = spec.partition_column
    dates = _partition_dates_from_where(stmt.args.get("where"), part)
    fresh = _freshness(
        sql,
        spec,
        catalog,
        dates,
        missing_ok=False,
        missing_message=(
            f"UPDATE 必须在 WHERE 中约束分区列 {part}，避免误写过期分区"
            if part
            else "缺少分区约束"
        ),
    )
    if fresh:
        return fresh
    return _ok(sql, f"UPDATE 通过门禁: 表 {spec.name}，列 {cols}")


def _check_delete(sql: str, stmt: exp.Delete, catalog: Catalog) -> Evidence:
    table_name = _ident(stmt.this)
    spec = _lookup_table(catalog, table_name, sql)
    if isinstance(spec, Evidence):
        return spec

    part = spec.partition_column
    dates = _partition_dates_from_where(stmt.args.get("where"), part)
    fresh = _freshness(
        sql,
        spec,
        catalog,
        dates,
        missing_ok=False,
        missing_message=(
            f"DELETE 必须在 WHERE 中约束分区列 {part}，避免误删过期分区"
            if part
            else "缺少分区约束"
        ),
    )
    if fresh:
        return fresh
    return _ok(sql, f"DELETE 通过门禁: 表 {spec.name}")
