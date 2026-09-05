"""JSONL approval queue. REQUIRE_APPROVAL SQL is recorded and not executed."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from write_gate.decision import Decision
from write_gate.paths import default_approvals_path, default_log_dir

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

_ID_LEN = 12


class ApprovalError(Exception):
    """Missing, not pending, or invalid approval record."""


@dataclass
class ApprovalRecord:
    id: str
    status: str
    sql: str
    database: str | None = None
    db_path: str | None = None
    policy_path: str | None = None
    catalog_path: str | None = None
    created_at: str = ""
    decision: dict[str, Any] = field(default_factory=dict)
    backend: str | None = None
    agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "sql": self.sql,
            "database": self.database,
            "db_path": self.db_path,
            "policy_path": self.policy_path,
            "catalog_path": self.catalog_path,
            "created_at": self.created_at,
            "decision": self.decision,
            "backend": self.backend,
            "agent": self.agent,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApprovalRecord":
        decision = raw.get("decision")
        if not isinstance(decision, dict):
            decision = {}
        return cls(
            id=str(raw.get("id") or ""),
            status=str(raw.get("status") or STATUS_PENDING),
            sql=str(raw.get("sql") or ""),
            database=raw.get("database"),
            db_path=raw.get("db_path"),
            policy_path=raw.get("policy_path"),
            catalog_path=raw.get("catalog_path"),
            created_at=str(raw.get("created_at") or ""),
            decision=decision,
            backend=raw.get("backend"),
            agent=raw.get("agent"),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:_ID_LEN]


def _path(path: Path | str | None = None) -> Path:
    return Path(path) if path else default_approvals_path()


def _load(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        rec_id = str(raw.get("id") or "")
        if rec_id:
            records[rec_id] = raw
    return records


def _save(path: Path, records: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records.values())
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def get_approval(approval_id: str, path: Path | str | None = None) -> ApprovalRecord | None:
    dest = _path(path)
    raw = _load(dest).get(str(approval_id))
    if not raw:
        return None
    return ApprovalRecord.from_dict(raw)


def list_pending(path: Path | str | None = None) -> list[ApprovalRecord]:
    dest = _path(path)
    pending: list[ApprovalRecord] = []
    for raw in _load(dest).values():
        rec = ApprovalRecord.from_dict(raw)
        if rec.status == STATUS_PENDING and rec.id:
            pending.append(rec)
    pending.sort(key=lambda r: r.created_at)
    return pending


def enqueue_approval(
    *,
    sql: str,
    decision: Decision,
    database: str | None = None,
    db_path: str | None = None,
    policy_path: str | None = None,
    catalog_path: str | None = None,
    path: Path | str | None = None,
    backend: str | None = None,
    agent: str | None = None,
) -> ApprovalRecord:
    dest = _path(path)
    records = _load(dest)
    approval_id = _new_id()
    while approval_id in records:
        approval_id = _new_id()
    rec = ApprovalRecord(
        id=approval_id,
        status=STATUS_PENDING,
        sql=sql,
        database=database,
        db_path=db_path,
        policy_path=str(policy_path) if policy_path else None,
        catalog_path=str(catalog_path) if catalog_path else None,
        created_at=_now(),
        decision=decision.to_dict(),
        backend=backend,
        agent=agent,
    )
    # Snapshot includes approval_id once attached on the live decision.
    rec.decision = {**rec.decision, "approval_id": approval_id}
    records[approval_id] = rec.to_dict()
    _save(dest, records)
    return rec


def set_status(
    approval_id: str,
    status: str,
    path: Path | str | None = None,
) -> ApprovalRecord:
    if status not in {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED}:
        raise ApprovalError(f"invalid approval status: {status}")
    dest = _path(path)
    records = _load(dest)
    raw = records.get(str(approval_id))
    if not raw:
        raise ApprovalError(f"approval not found: {approval_id}")
    rec = ApprovalRecord.from_dict(raw)
    if rec.status != STATUS_PENDING:
        raise ApprovalError(f"approval not pending: {approval_id}")
    rec.status = status
    records[rec.id] = rec.to_dict()
    _save(dest, records)
    return rec


def mark_approved(approval_id: str, path: Path | str | None = None) -> ApprovalRecord:
    return set_status(approval_id, STATUS_APPROVED, path=path)


def mark_rejected(approval_id: str, path: Path | str | None = None) -> ApprovalRecord:
    return set_status(approval_id, STATUS_REJECTED, path=path)


def ensure_log_dir() -> None:
    default_log_dir().mkdir(parents=True, exist_ok=True)
