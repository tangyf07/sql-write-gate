"""PII / restricted-column guard.

Writes to PII or restricted columns are BLOCK.
SELECT of PII columns is REQUIRE_APPROVAL (not silent allow).
SELECT of restricted columns is BLOCK.
"""

from __future__ import annotations

from write_gate.decision import RULE_PII, RULE_RESTRICTED, RISK_CRITICAL, RISK_MEDIUM, GuardResult

NAME = "pii"


def check_pii(ctx) -> GuardResult:
    parsed = ctx.parsed
    if parsed.statement is None or parsed.error:
        return GuardResult.pass_(NAME)

    table_name = parsed.table
    if not table_name:
        return GuardResult.pass_(NAME)
    spec = ctx.catalog.table(table_name)
    if spec is None:
        return GuardResult.pass_(NAME)

    if parsed.operation in {"insert", "update"}:
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

    if parsed.operation == "select":
        selected = _selected_columns(parsed, spec)
        restricted_hit = [c for c in selected if c in spec.restricted_columns]
        if restricted_hit:
            return GuardResult.block(
                NAME,
                RULE_RESTRICTED,
                f"禁止 SELECT 受限列 {restricted_hit}（表 {spec.name}）",
                risk=RISK_CRITICAL,
                evidence={"columns": restricted_hit, "table": spec.name},
            )
        pii_hit = [c for c in selected if c in spec.pii_columns]
        if pii_hit:
            return GuardResult.approval(
                NAME,
                RULE_PII,
                (
                    f"SELECT 包含 PII 列 {pii_hit}，需要审批"
                    f"（表 {spec.name} 的 pii_columns={sorted(spec.pii_columns)}）"
                ),
                risk=RISK_MEDIUM,
                evidence={"columns": pii_hit, "table": spec.name},
            )
    return GuardResult.pass_(NAME)


def _selected_columns(parsed, spec) -> list[str]:
    if parsed.star:
        return list(spec.columns.keys())
    return list(parsed.select_columns or parsed.columns)
