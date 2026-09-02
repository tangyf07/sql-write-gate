"""CLI: sql-write-gate check|exec|audit|hook|mcp|proxy|approve|reject|pending."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from write_gate.approvals import (
    ApprovalError,
    get_approval,
    list_pending,
    mark_rejected,
)
from write_gate.audit import format_audit_table, read_audit
from write_gate.decision import ACTION_ALLOW, ACTION_APPROVAL, ACTION_BLOCK, Decision
from write_gate.paths import APPROVALS_PATH, AUDIT_PATH
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
    if decision.approval_id:
        lines.append(f"Approval id: {decision.approval_id}")
    return "\n".join(lines)


def _safe(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _approvals_path(args: argparse.Namespace) -> Path:
    raw = getattr(args, "approvals", None)
    return Path(raw) if raw else APPROVALS_PATH


def _gate_from_args(args: argparse.Namespace) -> WriteGate:
    return WriteGate(
        db_path=Path(args.db) if getattr(args, "db", None) else None,
        database=getattr(args, "database", None),
        catalog_path=Path(args.catalog) if getattr(args, "catalog", None) else None,
        policy_path=Path(args.policy) if getattr(args, "policy", None) else None,
        approvals_path=_approvals_path(args),
        agent=getattr(args, "agent", None) or "cli",
    )


def _gate_from_record(rec, *, approvals_path: Path, agent: str = "approve") -> WriteGate:
    return WriteGate(
        database=rec.database,
        db_path=Path(rec.db_path) if rec.db_path else None,
        catalog_path=Path(rec.catalog_path) if rec.catalog_path else None,
        policy_path=Path(rec.policy_path) if rec.policy_path else None,
        approvals_path=approvals_path,
        agent=agent,
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
    parser.add_argument(
        "--database",
        help=(
            "DuckDB file path or postgres:// / postgresql:// URL "
            "(default: DATABASE_URL, then local DuckDB)"
        ),
    )
    parser.add_argument("--agent", default="cli", help="Audit agent name")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--approvals",
        help="Path to approvals jsonl (default: .logs/approvals.jsonl)",
    )


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

    hook_p = sub.add_parser(
        "hook",
        help="PreToolUse: block raw DB CLIs; never execute SQL",
        parents=[shared],
    )
    hook_p.add_argument(
        "--command",
        dest="hook_command",
        default=None,
        help="Bash command (tests / override stdin JSON)",
    )
    hook_p.add_argument(
        "--sql",
        dest="hook_sql",
        default=None,
        help="Raw SQL to evaluate (tests; still never executed)",
    )

    sub.add_parser(
        "mcp",
        help="Start MCP stdio server (query_sql / write_sql; ALLOW executes)",
        parents=[shared],
    )

    proxy_p = sub.add_parser(
        "proxy",
        help="Front a real DB: gate SQL then execute if ALLOW",
        parents=[shared],
    )
    proxy_p.add_argument(
        "--sql",
        dest="proxy_sql",
        default=None,
        help="One SQL statement then exit",
    )
    proxy_p.add_argument(
        "--once",
        action="store_true",
        help="Exit after one statement (default when --sql is set; stdin also exits on EOF)",
    )
    proxy_p.add_argument(
        "--listen",
        default=None,
        metavar="HOST:PORT",
        help="Text protocol: one SQL per connection then close (tests: 127.0.0.1:0)",
    )

    queue = argparse.ArgumentParser(add_help=False)
    queue.add_argument(
        "--approvals",
        help="Path to approvals jsonl (default: .logs/approvals.jsonl)",
    )
    queue.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    approve_p = sub.add_parser(
        "approve",
        help="Execute a pending approval id (re-runs guards; env approval only is cleared)",
        parents=[queue],
    )
    approve_p.add_argument("approval_id", help="Pending approval id")

    reject_p = sub.add_parser(
        "reject",
        help="Reject a pending approval id without writing",
        parents=[queue],
    )
    reject_p.add_argument("approval_id", help="Pending approval id")

    sub.add_parser(
        "pending",
        help="List pending approval ids",
        parents=[queue],
    )
    return parser


def _read_hook_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def _cmd_hook(args: argparse.Namespace) -> int:
    from write_gate.hooks import run_hook

    agent = getattr(args, "agent", None)
    if not agent or agent == "cli":
        agent = "hook"
    stdin_text = None
    if not args.hook_command and not args.hook_sql:
        stdin_text = _read_hook_stdin()
    return run_hook(
        bash_command=args.hook_command,
        sql=args.hook_sql,
        stdin_text=stdin_text,
        database=getattr(args, "database", None),
        db=getattr(args, "db", None),
        catalog=getattr(args, "catalog", None),
        policy=getattr(args, "policy", None),
        agent=agent,
    )


def _cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from write_gate.mcp_server import run_server
    except ImportError:
        sys.stderr.write('pip install -e ".[mcp]"\n')
        return 1
    agent = getattr(args, "agent", None)
    if not agent or agent == "cli":
        agent = "mcp"
    try:
        run_server(
            database=getattr(args, "database", None),
            db=getattr(args, "db", None),
            catalog=getattr(args, "catalog", None),
            policy=getattr(args, "policy", None),
            agent=agent,
        )
    except ImportError:
        sys.stderr.write('pip install -e ".[mcp]"\n')
        return 1
    return 0


def _cmd_proxy(args: argparse.Namespace) -> int:
    from write_gate.proxy import run_cli

    agent = getattr(args, "agent", None)
    if not agent or agent == "cli":
        args.agent = "proxy"
    with _gate_from_args(args) as gate:
        return run_cli(
            gate,
            sql=getattr(args, "proxy_sql", None),
            listen=getattr(args, "listen", None),
            once=bool(getattr(args, "once", False)),
            as_json=bool(getattr(args, "json", False)),
        )


def _cmd_approve(args: argparse.Namespace) -> int:
    path = _approvals_path(args)
    rec = get_approval(args.approval_id, path=path)
    if rec is None or rec.status != "pending":
        sys.stderr.write(f"approval not found or not pending: {args.approval_id}\n")
        return 1
    with _gate_from_record(rec, approvals_path=path, agent="approve") as gate:
        try:
            decision, result = gate.approve(rec.id)
        except ApprovalError as exc:
            sys.stderr.write(str(exc) + "\n")
            return 1
    return _print_decision(decision, as_json=bool(args.json), result=result)


def _cmd_reject(args: argparse.Namespace) -> int:
    path = _approvals_path(args)
    rec = get_approval(args.approval_id, path=path)
    if rec is None or rec.status != "pending":
        sys.stderr.write(f"approval not found or not pending: {args.approval_id}\n")
        return 1
    try:
        rec = mark_rejected(rec.id, path=path)
    except ApprovalError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    if args.json:
        json.dump(rec.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(f"REJECTED\nApproval id: {rec.id}\n")
    return 0


def _cmd_pending(args: argparse.Namespace) -> int:
    path = _approvals_path(args)
    rows = list_pending(path=path)
    if args.json:
        json.dump([r.to_dict() for r in rows], sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not rows:
        sys.stdout.write("(no pending approvals)\n")
        return 0
    for rec in rows:
        decision = rec.decision if isinstance(rec.decision, dict) else {}
        op = decision.get("operation") or "-"
        table = decision.get("table") or "-"
        sys.stdout.write(f"{rec.id}  {rec.status}  {op}  {table}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit":
        path = Path(args.audit_path) if args.audit_path else AUDIT_PATH
        rows = read_audit(path, limit=args.limit)
        sys.stdout.write(format_audit_table(rows) + "\n")
        return 0

    if args.command == "hook":
        return _cmd_hook(args)

    if args.command == "mcp":
        return _cmd_mcp(args)

    if args.command == "proxy":
        return _cmd_proxy(args)

    if args.command == "approve":
        return _cmd_approve(args)

    if args.command == "reject":
        return _cmd_reject(args)

    if args.command == "pending":
        return _cmd_pending(args)

    with _gate_from_args(args) as gate:
        if args.command == "check":
            decision = gate.check(args.sql)
            result = None
        else:
            decision, result = gate.execute(args.sql)
    return _print_decision(decision, as_json=bool(args.json), result=result)


if __name__ == "__main__":
    raise SystemExit(main())
