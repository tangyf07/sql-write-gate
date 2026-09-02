"""Blast-radius guard: UPDATE/DELETE estimated rows vs policy limits."""

import duckdb
import pytest

from write_gate.catalog import load_catalog
from write_gate.config import Policy
from write_gate.db import ORDERS_DDL
from write_gate.engine import evaluate
from write_gate.wrapper import WriteGate


def _policy(**limits) -> Policy:
    return Policy(
        environment="test",
        rules={
            "select": "allow",
            "insert": "allow",
            "update": "allow",
            "delete": "allow",
            "ddl": "block",
        },
        update_rows=limits.get("update_rows", 2),
        delete_rows=limits.get("delete_rows", 2),
    )


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    c.execute(ORDERS_DDL)
    c.execute(
        "INSERT INTO orders VALUES "
        "(1, 1001, 12.5, DATE '2026-09-01', 'a@example.com', '13800000001', 'paid'),"
        "(2, 1002, 9.9, DATE '2026-09-01', 'b@example.com', '13800000002', 'pending'),"
        "(3, 1003, 8.1, DATE '2026-09-01', 'c@example.com', '13800000003', 'paid'),"
        "(4, 1004, 7.0, DATE '2026-08-01', 'd@example.com', '13800000004', 'paid')"
    )
    yield c
    c.close()


def test_update_blast_radius_exceeded(conn):
    ev = evaluate(
        "UPDATE orders SET status = 'expired' WHERE dt = '2026-09-01'",
        load_catalog(),
        policy=_policy(update_rows=2),
        conn=conn,
    )
    assert ev.action == "BLOCK"
    assert ev.rule_id == "blast_radius_exceeded"
    assert ev.estimated_rows == 3
    assert ev.evidence.get("estimated_rows") == 3
    assert ev.evidence.get("max_rows") == 2


def test_delete_blast_radius_exceeded(conn):
    ev = evaluate(
        "DELETE FROM orders WHERE dt = '2026-09-01'",
        load_catalog(),
        policy=_policy(delete_rows=1),
        conn=conn,
    )
    assert ev.action == "BLOCK"
    assert ev.rule_id == "blast_radius_exceeded"
    assert ev.estimated_rows == 3
    assert ev.evidence.get("max_rows") == 1


def test_update_within_limit_allowed(conn):
    ev = evaluate(
        "UPDATE orders SET status = 'paid' WHERE order_id = 1",
        load_catalog(),
        policy=_policy(update_rows=2),
        conn=conn,
    )
    assert ev.action == "ALLOW"
    assert ev.rule_id == "ok"
    assert ev.estimated_rows == 1


def test_execute_blocked_does_not_write(conn, tmp_path):
    gate = WriteGate(
        db_path=tmp_path / "wh.duckdb",
        conn=conn,
        policy=_policy(update_rows=1),
        audit_path=tmp_path / "audit.jsonl",
    )
    before = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'expired'").fetchone()[0]
    ev, result = gate.execute(
        "UPDATE orders SET status = 'expired' WHERE dt = '2026-09-01'"
    )
    after = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'expired'").fetchone()[0]
    assert ev.rule_id == "blast_radius_exceeded"
    assert result is None
    assert after == before
