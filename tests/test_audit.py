"""Audit log JSONL + CLI table."""

from write_gate.audit import append_audit, format_audit_table, read_audit
from write_gate.cases import LEGAL_WRITE_SQL
from write_gate.config import demo_policy
from write_gate.wrapper import WriteGate


def test_check_appends_audit_jsonl(tmp_path):
    audit = tmp_path / "audit.jsonl"
    gate = WriteGate(
        db_path=tmp_path / "wh.duckdb",
        policy=demo_policy(),
        audit_path=audit,
        agent="test",
    )
    ev = gate.check(LEGAL_WRITE_SQL)
    assert ev.action == "ALLOW"
    rows = read_audit(audit, limit=10)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["agent"] == "test"
    assert rec["environment"] == "demo"
    assert rec["sql"] == LEGAL_WRITE_SQL
    assert rec["operation"] == "insert"
    assert rec["table"] == "orders"
    assert rec["decision"] == "ALLOW"
    assert rec["rule_id"] == "ok"
    assert "timestamp" in rec
    table = format_audit_table(rows)
    assert "ALLOW" in table
    assert "insert" in table
