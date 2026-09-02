"""CLI: sql-write-gate check|exec|audit. Also used by python -m write_gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from write_gate.audit import format_audit_table, read_audit
from write_gate.decision import ACTION_ALLOW, ACTION_APPROVAL, ACTION_BLOCK, Decision
from write_gate.paths import AUDIT_PATH
from write_gate.wrapper import WriteGate


def _headline(action: str) -> str:
    if action == ACTION_BLOCK:
        return "BLOCKED"
    if action == ACTION_APPROVAL:
        return "APPROVAL REQUIRED"
    return "ALLOWED"


def format_decision(decision: Decision) -> str:
    lines = [
        _headline(decision.action),
        f"Risk: {_safe(decision.risk)}",
        f"Operation: {_safe(decision.operation).upper()}",
        f"Table: {_safe(decision.table)}",
        f"Rule: {_safe(decision.rule_id)}",
        f"Reason: {_safe(decision.reason)}",
    ]
    if decision.estimated_rows is not None:
        lines.append(f"Estimated rows: {decision.estimated_rows}")
    return "\n".join(lines)


def _safe(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _gate_from_args(args: argparse.Namespace) -> WriteGate:
    return WriteGate(
        db_path=Path(args.db) if getattr(args, "db", None) else None,
        catalog_path=Path(args.catalog) if getattr(args, "catalog", None) else None,
        policy_path=Path(args.policy) if getattr(args, "policy", None) else None,
        agent=getattr(args, "agent", None) or "cli",
    )


def _print_decision(decision: Decision, *, as_json: bool, result=None) -> int:
    if as_json:
        payload = decision.to_dict()
        if result is not None and decision.allowed:
            try:
                rows = result.fetchall()
                payload["rows"] = [list(r) for r in rows]
                payload["rowcount"] = len(payload["rows"])
            except Exception:
                payload["rowcount"] = getattr(result, "rowcount", None)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_decision(decision) + "\n")
    if decision.action == ACTION_ALLOW:
        return 0
    if decision.action == ACTION_APPROVAL:
        return 1
    return 2


def _add_shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", help="Path to policy.yaml (default: ./policy.yaml)")
    parser.add_argument("--catalog", help="Path to catalog.json")
    parser.add_argument("--db", help="Path to DuckDB warehouse")
    parser.add_argument("--agent", default="cli", help="Audit agent name")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    _add_shared(shared)
    parser = argparse.ArgumentParser(
        prog="sql-write-gate",
        description="Policy firewall for AI agents writing to databases (no LLM, no API key)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Evaluate SQL without executing", parents=[shared])
    check_p.add_argument("sql", help="One SQL statement")

    exec_p = sub.add_parser("exec", help="Evaluate then execute if ALLOW", parents=[shared])
    exec_p.add_argument("sql", help="One SQL statement")

    audit_p = sub.add_parser("audit", help="Print recent audit log rows")
    audit_p.add_argument("--limit", type=int, default=20, help="How many recent rows")
    audit_p.add_argument("--audit-path", default=None, help="Override audit jsonl path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit":
        path = Path(args.audit_path) if args.audit_path else AUDIT_PATH
        rows = read_audit(path, limit=args.limit)
        sys.stdout.write(format_audit_table(rows) + "\n")
        return 0

    with _gate_from_args(args) as gate:
        if args.command == "check":
            decision = gate.check(args.sql)
            result = None
        else:
            decision, result = gate.execute(args.sql)
    return _print_decision(decision, as_json=bool(args.json), result=result)


if __name__ == "__main__":
    raise SystemExit(main())
