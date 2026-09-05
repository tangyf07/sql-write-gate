"""PII / restricted-column guard.

Writes to PII or restricted columns are BLOCK.
SELECT of PII columns is REQUIRE_APPROVAL (not silent allow).
SELECT of restricted columns is BLOCK.

Column discovery walks the full AST (CTE bodies, UNION arms, expressions)
so wrappers like WITH / UNION / concat(email, ...) cannot bypass checks.
"""

from __future__ import annotations

from sqlglot import exp

from write_gate.decision import RULE_PII, RULE_RESTRICTED, RISK_CRITICAL, RISK_MEDIUM, GuardResult
from write_gate.parser import ident

NAME = "pii"


def check_pii(ctx) -> GuardResult:
    parsed = ctx.parsed
    if parsed.statement is None or parsed.error:
        return GuardResult.pass_(NAME)

    if parsed.operation in {"insert", "update"}:
        return _check_write(parsed, ctx.catalog)

    if parsed.operation == "select":
        return _check_select(parsed, ctx.catalog)

    return GuardResult.pass_(NAME)


def _check_write(parsed, catalog) -> GuardResult:
    table_name = parsed.table
    if not table_name:
        return GuardResult.pass_(NAME)
    spec = catalog.table(table_name)
    if spec is None:
        return GuardResult.pass_(NAME)

    cols = list(parsed.write_columns)
    restricted_hit = [c for c in cols if c in spec.restricted_columns]
    if restricted_hit:
        return GuardResult.block(
            NAME,
            RULE_RESTRICTED,
            (
                f"禁止写入受限列 {restricted_hit}"
                f"（表 {spec.name} 的 restricted_columns={sorted(spec.restricted_columns)}）"
            ),
            risk=RISK_CRITICAL,
            evidence={"columns": restricted_hit, "table": spec.name},
        )
    pii_hit = [c for c in cols if c in spec.pii_columns]
    if pii_hit:
        return GuardResult.block(
            NAME,
            RULE_PII,
            (
                f"禁止写入 PII 列 {pii_hit}"
                f"（表 {spec.name} 的 pii_columns={sorted(spec.pii_columns)}）"
            ),
            risk=RISK_CRITICAL,
            evidence={"columns": pii_hit, "table": spec.name},
        )
    return GuardResult.pass_(NAME)


def _check_select(parsed, catalog) -> GuardResult:
    selected, table_hint = _selected_columns(parsed, catalog)
    specs = _specs_for_select(parsed, catalog, table_hint)
    if not specs:
        # No catalog table resolved — still match known PII/restricted names
        # across the whole catalog so CTE/UNION wrappers cannot silent-ALLOW.
        specs = list(catalog.tables.values())
    if not specs:
        return GuardResult.pass_(NAME)

    restricted_hit: list[str] = []
    pii_hit: list[str] = []
    hit_table = None
    for spec in specs:
        for col in selected:
            if col in spec.restricted_columns and col not in restricted_hit:
                restricted_hit.append(col)
                hit_table = hit_table or spec.name
            if col in spec.pii_columns and col not in pii_hit:
                pii_hit.append(col)
                hit_table = hit_table or spec.name

    if restricted_hit:
        return GuardResult.block(
            NAME,
            RULE_RESTRICTED,
            f"禁止 SELECT 受限列 {restricted_hit}（表 {hit_table}）",
            risk=RISK_CRITICAL,
            evidence={"columns": restricted_hit, "table": hit_table},
        )
    if pii_hit:
        pii_cols = sorted(
            {c for spec in specs for c in spec.pii_columns if c in pii_hit}
        )
        # Prefer the table that owns the hit for messaging.
        owner = hit_table
        for spec in specs:
            if any(c in spec.pii_columns for c in pii_hit):
                owner = spec.name
                pii_cols = sorted(spec.pii_columns)
                break
        return GuardResult.approval(
            NAME,
            RULE_PII,
            (
                f"SELECT 包含 PII 列 {pii_hit}，需要审批"
                f"（表 {owner} 的 pii_columns={pii_cols}）"
            ),
            risk=RISK_MEDIUM,
            evidence={"columns": pii_hit, "table": owner},
        )
    return GuardResult.pass_(NAME)


def _specs_for_select(parsed, catalog, table_hint: str | None):
    specs = []
    seen = set()
    stmt = parsed.statement
    candidates: list[str] = []
    if table_hint:
        candidates.append(table_hint)
    if parsed.table:
        candidates.append(parsed.table)
    if stmt is not None:
        for table in stmt.find_all(exp.Table):
            name = ident(table)
            if name:
                candidates.append(name)
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        spec = catalog.table(name)
        if spec is not None:
            specs.append(spec)
    return specs


def _selected_columns(parsed, catalog) -> tuple[list[str], str | None]:
    """Return (column names, primary catalog table hint)."""
    stmt = parsed.statement
    cols: list[str] = []
    table_hint = parsed.table if parsed.table and catalog.table(parsed.table) else None

    if parsed.select_columns:
        cols.extend(parsed.select_columns)
    elif parsed.columns:
        cols.extend(parsed.columns)

    if stmt is not None:
        for col in stmt.find_all(exp.Column):
            name = ident(col)
            if name and name != "*":
                cols.append(name)

        # Expand SELECT * against catalog tables (and CTE outputs that name PII).
        for select in stmt.find_all(exp.Select):
            has_star = any(
                isinstance(item, exp.Star)
                or (isinstance(item, exp.Column) and getattr(item, "is_star", False))
                for item in (select.expressions or [])
            )
            if not has_star:
                continue
            from_ = select.args.get("from_") or select.args.get("from")
            src = None
            if from_ is not None:
                this = from_.this if isinstance(from_, exp.From) else from_
                src = ident(this)
            if src and catalog.table(src):
                table_hint = table_hint or src
                cols.extend(catalog.table(src).columns.keys())
            elif src:
                # CTE alias: columns already collected from CTE body via parser.
                pass

    if parsed.star and table_hint and catalog.table(table_hint):
        cols.extend(catalog.table(table_hint).columns.keys())

    # Deduplicate while preserving order.
    return list(dict.fromkeys(cols)), table_hint
