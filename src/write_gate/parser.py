"""Parse a single SQL statement into a structured form (sqlglot AST, no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import sqlglot
from sqlglot import exp

from write_gate.decision import RULE_SCHEMA, RULE_UNSUPPORTED

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WRITE_TYPE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "Command",
    "Copy",
    "TruncateTable",
    "Replace",
)

DDL_TYPE_NAMES = (
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Command",
)


@dataclass
class ParsedSQL:
    sql: str
    statement: exp.Expression | None = None
    operation: str = "unknown"  # select | insert | update | delete | ddl | unknown
    table: str | None = None
    table_alias: str | None = None
    columns: list[str] = field(default_factory=list)
    insert_columns: list[str] = field(default_factory=list)
    write_columns: list[str] = field(default_factory=list)
    select_columns: list[str] = field(default_factory=list)
    star: bool = False
    where: exp.Expression | None = None
    has_where: bool = False
    assignments: dict[str, exp.Expression | None] = field(default_factory=dict)
    insert_rows: list[list[exp.Expression]] | None = None
    error: str | None = None
    error_rule: str = RULE_SCHEMA


def ident(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, exp.Table):
        return node.name.lower() if node.name else None
    if isinstance(node, exp.Schema):
        return ident(node.this)
    if isinstance(node, exp.Identifier):
        return node.name.lower()
    if isinstance(node, exp.Column):
        return node.name.lower() if node.name else None
    name = getattr(node, "name", None)
    return str(name).lower() if name else None


def literal_value(node: exp.Expression | None) -> Any:
    if node is None:
        return None
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Cast):
        return literal_value(node.this)
    if isinstance(node, (exp.TsOrDsToDate, exp.Date)):
        return literal_value(node.this) if node.this else node.sql()
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
    sql = node.sql(dialect="duckdb").strip().strip("'\"")
    return sql


def expected_kind(col_type: str) -> str:
    t = col_type.upper()
    if t in {"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT"}:
        return "INTEGER"
    if t in {"DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"}:
        return "DOUBLE"
    if t in {"DATE", "TIMESTAMP"}:
        return "DATE"
    return "VARCHAR"


def type_ok(expected: str, node: exp.Expression | None) -> bool:
    if node is None or isinstance(node, exp.Null):
        return True
    kind = expected_kind(expected)
    value = literal_value(node)
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


def parse_date(value: Any) -> date | None:
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


def _write_types() -> tuple[type, ...]:
    return tuple(getattr(exp, n) for n in WRITE_TYPE_NAMES if hasattr(exp, n))


def _ddl_types() -> tuple[type, ...]:
    return tuple(getattr(exp, n) for n in DDL_TYPE_NAMES if hasattr(exp, n))


def _has_select_into(stmt: exp.Expression) -> bool:
    """PostgreSQL SELECT ... INTO is a write (creates a table), not read-only."""
    if isinstance(stmt, exp.Select) and stmt.args.get("into") is not None:
        return True
    into_cls = getattr(exp, "Into", None)
    if into_cls is None:
        return False
    return any(isinstance(n, into_cls) for n in stmt.find_all(into_cls))


def _nested_dml_nodes(stmt: exp.Expression) -> list[exp.Expression]:
    """DML nested under a non-DML root (e.g. data-modifying CTE)."""
    write_types = _write_types()
    if not write_types:
        return []
    if isinstance(stmt, write_types):
        return []
    return [n for n in stmt.find_all(*write_types) if n is not stmt]


def is_read_only(stmt: exp.Expression) -> bool:
    write_types = _write_types()
    if write_types and isinstance(stmt, write_types):
        return False
    if _has_select_into(stmt):
        return False
    if _nested_dml_nodes(stmt):
        return False
    if isinstance(stmt, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return True
    return isinstance(stmt, exp.Query) and not isinstance(stmt, write_types)


def unsupported_read_reason(stmt: exp.Expression) -> tuple[str, str] | None:
    """Explicit reject reasons for structures that must never silent-ALLOW as read-only."""
    if _has_select_into(stmt):
        return (
            RULE_UNSUPPORTED,
            "SELECT INTO is not supported as read-only; rejected (unsupported_sql)",
        )
    nested = _nested_dml_nodes(stmt)
    if nested:
        kinds = sorted({type(n).__name__ for n in nested})
        return (
            RULE_UNSUPPORTED,
            (
                "Data-modifying CTE / nested DML "
                f"({', '.join(kinds)}) is not supported as read-only; "
                "rejected (unsupported_sql)"
            ),
        )
    return None


def classify_operation(stmt: exp.Expression) -> str:
    if isinstance(stmt, exp.Insert):
        return "insert"
    if isinstance(stmt, exp.Update):
        return "update"
    if isinstance(stmt, exp.Delete):
        return "delete"
    ddl_types = _ddl_types()
    if ddl_types and isinstance(stmt, ddl_types):
        return "ddl"
    if isinstance(stmt, exp.Merge):
        return "ddl"
    if _has_select_into(stmt):
        return "ddl"
    nested = _nested_dml_nodes(stmt)
    if nested:
        # Prefer the nested DML kind for policy; schema/unsupported still reject.
        inner = nested[0]
        if isinstance(inner, exp.Insert):
            return "insert"
        if isinstance(inner, exp.Update):
            return "update"
        if isinstance(inner, exp.Delete):
            return "delete"
        return "ddl"
    if is_read_only(stmt):
        return "select"
    return "ddl"


def _table_from_select(stmt: exp.Expression) -> str | None:
    from_ = stmt.args.get("from_") or stmt.args.get("from")
    if from_ is None:
        return None
    this = from_.this if isinstance(from_, exp.From) else from_
    return ident(this)


def _table_from_truncate(stmt: exp.Expression) -> str | None:
    for item in stmt.expressions or []:
        name = ident(item)
        if name:
            return name
    return ident(stmt.this)


def extract_table(stmt: exp.Expression) -> str | None:
    if isinstance(stmt, exp.TruncateTable) or type(stmt).__name__ == "TruncateTable":
        return _table_from_truncate(stmt)
    if isinstance(stmt, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return _table_from_select(stmt)
    if isinstance(stmt, exp.Query):
        return _table_from_select(stmt)
    return ident(stmt.this)


def extract_where(stmt: exp.Expression) -> exp.Expression | None:
    where = stmt.args.get("where")
    if where is None:
        return None
    if isinstance(where, exp.Where):
        return where.this
    return where


def where_sql(where: exp.Expression | None, dialect: str = "duckdb") -> str | None:
    if where is None:
        return None
    if isinstance(where, exp.Where):
        return where.this.sql(dialect=dialect) if where.this else None
    return where.sql(dialect=dialect)


def _column_names_in_expression(node: exp.Expression) -> list[str]:
    """Column identifiers referenced inside a projection / expression tree."""
    cols: list[str] = []
    for col in node.find_all(exp.Column):
        name = ident(col)
        if name and name != "*":
            cols.append(name)
    return cols


def _projection_columns(expressions: list[exp.Expression] | None) -> tuple[list[str], bool]:
    cols: list[str] = []
    star = False
    for item in expressions or []:
        if isinstance(item, exp.Star):
            star = True
            continue
        if isinstance(item, exp.Column) and getattr(item, "is_star", False):
            star = True
            continue
        # Prefer underlying column refs (covers concat(email, phone) AS contact).
        nested = _column_names_in_expression(item)
        if nested:
            cols.extend(nested)
            continue
        if isinstance(item, exp.Alias):
            name = ident(item.this) or ident(item)
        else:
            name = ident(item)
        if name and name != "*":
            cols.append(name)
        elif name == "*":
            star = True
    return cols, star


def _select_columns(stmt: exp.Expression) -> tuple[list[str], bool]:
    """Collect projected / referenced columns for PII checks (CTE, UNION, exprs)."""
    cols: list[str] = []
    star = False

    # UNION / EXCEPT / INTERSECT: walk both sides.
    if isinstance(stmt, (exp.Union, exp.Except, exp.Intersect)):
        for side in (stmt.this, stmt.expression):
            if side is None:
                continue
            c, s = _select_columns(side)
            cols.extend(c)
            star = star or s
        return list(dict.fromkeys(cols)), star

    # WITH ... AS (...): include CTE bodies so SELECT * FROM cte still sees PII.
    for cte in stmt.find_all(exp.CTE):
        body = cte.this
        if body is None:
            continue
        c, s = _select_columns(body)
        cols.extend(c)
        star = star or s

    if isinstance(stmt, exp.Select):
        c, s = _projection_columns(stmt.expressions)
        cols.extend(c)
        star = star or s
    elif hasattr(stmt, "expressions"):
        c, s = _projection_columns(stmt.expressions)
        cols.extend(c)
        star = star or s

    return list(dict.fromkeys(cols)), star


def table_alias(stmt: exp.Expression) -> str | None:
    """Alias on the target table of UPDATE/DELETE (for COUNT estimates)."""
    target = stmt.this if isinstance(stmt, (exp.Update, exp.Delete)) else None
    if isinstance(target, exp.Table):
        alias = target.args.get("alias")
        if isinstance(alias, exp.TableAlias):
            return ident(alias.this) or ident(alias)
        if alias is not None:
            return ident(alias)
        return target.alias if getattr(target, "alias", None) else None
    return None


def conflict_update_columns(stmt: exp.Insert) -> list[str]:
    """Columns written by ON CONFLICT ... DO UPDATE SET (UPSERT)."""
    conflict = stmt.args.get("conflict")
    if conflict is None:
        return []
    cols: list[str] = []
    for item in conflict.expressions or []:
        if isinstance(item, exp.EQ):
            name = ident(item.this)
            if name:
                cols.append(name)
        else:
            name = ident(item)
            if name:
                cols.append(name)
    return cols


def insert_columns(stmt: exp.Insert, fallback: list[str] | None = None) -> list[str]:
    target = stmt.this
    if isinstance(target, exp.Schema) and target.expressions:
        return [ident(c) or "" for c in target.expressions]
    return list(fallback or [])


def insert_rows(stmt: exp.Insert) -> list[list[exp.Expression]] | None:
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


def update_assignments(stmt: exp.Update) -> dict[str, exp.Expression | None]:
    out: dict[str, exp.Expression | None] = {}
    for item in stmt.expressions:
        if isinstance(item, exp.EQ):
            name = ident(item.this)
            if name:
                out[name] = item.expression
    return out


def partition_dates_from_where(
    where: exp.Expression | None, part: str | None
) -> list[date | None]:
    if where is None or not part:
        return []
    dates: list[date | None] = []

    def visit(node: exp.Expression) -> None:
        if isinstance(node, exp.In) and ident(node.this) == part:
            for item in node.expressions:
                dates.append(parse_date(literal_value(item)))
            return
        if isinstance(node, exp.EQ):
            left, right = node.this, node.expression
            if ident(left) == part:
                dates.append(parse_date(literal_value(right)))
            elif ident(right) == part:
                dates.append(parse_date(literal_value(left)))
            return
        for child in node.iter_expressions():
            visit(child)

    visit(where)
    return dates


def partition_dates_from_insert(
    cols: list[str], rows: list[list[exp.Expression]], part: str | None
) -> list[date | None]:
    if not part:
        return []
    dates: list[date | None] = []
    for row in rows:
        assignments = dict(zip(cols, row))
        if part not in assignments:
            dates.append(None)
        else:
            dates.append(parse_date(literal_value(assignments[part])))
    return dates


def parse(sql: str, dialect: str = "duckdb") -> ParsedSQL:
    original = sql
    stripped = sql.strip().rstrip(";").strip()
    parsed = ParsedSQL(sql=original)
    if not stripped:
        parsed.error = "SQL 为空，无法执行"
        return parsed

    read_dialect = dialect or "duckdb"
    try:
        statements = sqlglot.parse(stripped, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        if read_dialect != "duckdb":
            try:
                statements = sqlglot.parse(stripped, read="duckdb")
            except sqlglot.errors.ParseError as exc2:
                parsed.error = f"SQL 无法解析: {exc2}"
                return parsed
        else:
            parsed.error = f"SQL 无法解析: {exc}"
            return parsed

    statements = [s for s in statements if s is not None]
    if not statements:
        parsed.error = "SQL 无法解析为空语句"
        return parsed
    if len(statements) != 1:
        parsed.error = f"一次只允许一条语句，收到 {len(statements)} 条"
        return parsed

    stmt = statements[0]
    parsed.statement = stmt

    unsupported = unsupported_read_reason(stmt)
    if unsupported:
        rule, reason = unsupported
        parsed.error = reason
        parsed.error_rule = rule
        parsed.operation = classify_operation(stmt)
        parsed.table = extract_table(stmt)
        return parsed

    parsed.operation = classify_operation(stmt)
    parsed.table = extract_table(stmt)
    parsed.table_alias = table_alias(stmt)
    parsed.where = extract_where(stmt)
    parsed.has_where = parsed.where is not None

    if isinstance(stmt, exp.Insert):
        cols = insert_columns(stmt)
        conflict_cols = conflict_update_columns(stmt)
        # UPSERT: insert_columns = INSERT target list (VALUES arity);
        # write_columns = insert + ON CONFLICT DO UPDATE SET (PII/schema writes).
        write_cols = list(dict.fromkeys([*cols, *conflict_cols]))
        parsed.insert_columns = list(cols)
        parsed.write_columns = write_cols
        parsed.columns = list(cols)
        parsed.insert_rows = insert_rows(stmt)
        parsed.assignments = {}
        if conflict_cols:
            # Keep SET expressions for type checks when present.
            conflict = stmt.args.get("conflict")
            for item in (conflict.expressions or [] if conflict is not None else []):
                if isinstance(item, exp.EQ):
                    name = ident(item.this)
                    if name:
                        parsed.assignments[name] = item.expression
    elif isinstance(stmt, exp.Update):
        assignments = update_assignments(stmt)
        parsed.assignments = assignments
        parsed.write_columns = list(assignments.keys())
        parsed.columns = list(assignments.keys())
    elif is_read_only(stmt):
        cols, star = _select_columns(stmt)
        parsed.select_columns = cols
        parsed.columns = cols
        parsed.star = star
    return parsed
