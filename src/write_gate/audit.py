"""JSONL audit log for every check/execute."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from write_gate.decision import ACTION_APPROVAL, Decision
from write_gate.paths import default_audit_path, default_log_dir

# Glanceable table times: UTC ISO on disk, Asia/Shanghai in `sql-write-gate audit`.
AUDIT_DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
EMPTY_AUDIT_MESSAGE = "(no audit records)"
VERDICT_DISPLAY = {
    ACTION_APPROVAL: "APPROVAL",
    "REQUIRE_APPROVAL": "APPROVAL",
}

# scheme://user:password@host → scheme://user:***@host
# Password may contain '@' if not percent-encoded; use last '@' before host.
_URL_PASSWORD = re.compile(
    r"^(?P<head>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s]+):"
    r"(?P<password>.+)"
    r"@(?P<host>[^/@?#\s]+)(?P<rest>.*)$"
)


def redact_database_url(value: object) -> str | None:
    """Redact password in DB URLs for audit / approvals logs."""
    if value is None:
        return None
    raw = str(value)
    if not raw or "://" not in raw or "@" not in raw:
        return raw
    m = _URL_PASSWORD.match(raw)
    if m:
        return f"{m.group('head')}:***@{m.group('host')}{m.group('rest')}"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.password is None:
        return raw
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = parts.username or ""
    netloc = f"{user}:***@{host}" if user else f"***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def append_audit(
    decision: Decision,
    *,
    agent: str = "cli",
    environment: str = "production",
    path: Path | None = None,
    database: str | None = None,
    executed: bool | None = None,
    execution_outcome: str | None = None,
) -> None:
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "environment": environment,
        "sql": decision.sql,
        "operation": decision.operation,
        "table": decision.table,
        "estimated_rows": decision.estimated_rows,
        "decision": decision.action,
        "rule_id": decision.rule_id,
    }
    if decision.approval_id:
        record["approval_id"] = decision.approval_id
    if database is not None:
        record["database"] = redact_database_url(database)
    if executed is not None:
        record["executed"] = executed
    if execution_outcome is not None:
        record["execution_outcome"] = execution_outcome
    dest = Path(path) if path else default_audit_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_audit(path: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    dest = Path(path) if path else default_audit_path()
    if not dest.exists():
        return []
    lines = dest.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def format_audit_time(value: object) -> str:
    """UTC ISO (with or without microseconds) → glanceable local `YYYY-MM-DD HH:MM`."""
    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:16].replace("T", " ")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(AUDIT_DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")


def format_verdict(value: object) -> str:
    """Map JSONL `decision` to the printed VERDICT column only."""
    raw = str(value or "").strip()
    if not raw:
        return "-"
    return VERDICT_DISPLAY.get(raw, raw)


def format_audit_table(rows: Iterable[dict[str, Any]]) -> str:
    records = list(rows)
    if not records:
        return EMPTY_AUDIT_MESSAGE
    headers = ("TIME", "SOURCE", "OP", "TABLE", "VERDICT", "RULE")
    extracted: list[tuple[str, ...]] = []
    for rec in records:
        extracted.append(
            (
                format_audit_time(rec.get("timestamp")),
                str(rec.get("agent") or "-"),
                str(rec.get("operation") or "-"),
                str(rec.get("table") or "-"),
                format_verdict(rec.get("decision")),
                str(rec.get("rule_id") or "-"),
            )
        )
    widths = [len(h) for h in headers]
    for row in extracted:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in extracted)
    return "\n".join(lines)


def ensure_log_dir() -> None:
    log_dir = default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = log_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")
