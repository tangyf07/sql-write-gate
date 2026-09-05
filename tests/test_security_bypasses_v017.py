"""v0.17.0 P0 security regression tests.

Former bypasses on 0.16.1 / main must BLOCK or REQUIRE_APPROVAL / REJECT —
never silent ALLOW as read-only. Blast-radius estimate failure fails closed.
"""

from __future__ import annotations

import duckdb
import pytest

from write_gate import WriteGate
from write_gate.catalog import load_catalog
from write_gate.config import Policy
from write_gate.db import ORDERS_DDL
from write_gate.engine import evaluate
from write_gate.guards.blast_radius import check_blast_radius
from write_gate.engine import Context
from write_gate.parser import parse


def _allow_policy(**limits) -> Policy:
    return Policy(
        environment="test",
        rules={
            "select": "allow",
            "insert": "allow",
            "update": "allow",
            "delete": "allow",
            "ddl": "block",
        },
        update_rows=limits.get("update_rows", 1),
        delete_rows=limits.get("delete_rows", 1),
    )


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    c.execute(ORDERS_DDL)
    c.execute(
        "INSERT INTO orders VALUES "
        "(1, 1001, 12.5, DATE '2026-09-01', 'a@example.com', '13800000001', 'paid'),"
        "(2, 1002, 9.9, DATE '2026-09-01', 'b@example.com', '13800000002', 'pending'),"
        "(3, 1003, 8.1, DATE '2026-09-01', 'c@example.com', '13800000003', 'paid')"
    )
    yield c
    c.close()


@pytest.fixture()
def catalog():
    return load_catalog()


# --- PII read wrappers (must not silent-ALLOW) ---------------------------------


def test_pii_via_cte_requires_approval(catalog):
    sql = "WITH x AS (SELECT email FROM orders) SELECT * FROM x"
    ev = evaluate(sql, catalog, policy=_allow_policy())
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.rule_id == "pii_column"
    assert ev.allowed is False


def test_pii_via_union_requires_approval(catalog):
    sql = "SELECT email FROM orders UNION ALL SELECT email FROM orders"
    ev = evaluate(sql, catalog, policy=_allow_policy())
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.rule_id == "pii_column"
    assert ev.allowed is False


def test_pii_via_expression_requires_approval(catalog):
    sql = "SELECT concat(email, phone) AS contact FROM orders"
    ev = evaluate(sql, catalog, policy=_allow_policy())
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.rule_id == "pii_column"
    assert "email" in (ev.evidence.get("columns") or []) or "email" in ev.reason
    assert ev.allowed is False


def test_plain_select_pii_still_requires_approval(catalog):
    ev = evaluate("SELECT email FROM orders", catalog, policy=_allow_policy())
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.rule_id == "pii_column"


# --- UPSERT ON CONFLICT DO UPDATE SET pii -------------------------------------


def test_upsert_conflict_update_pii_blocked(catalog):
    sql = (
        "INSERT INTO orders (order_id, user_id, amount, dt, status) "
        "VALUES (99, 1, 1.0, '2026-09-01', 'paid') "
        "ON CONFLICT (order_id) DO UPDATE SET email = 'x@y.com'"
    )
    parsed = parse(sql)
    assert parsed.insert_columns == [
        "order_id",
        "user_id",
        "amount",
        "dt",
        "status",
    ]
    assert "email" in parsed.write_columns
    assert len(parsed.insert_columns) == 5
    assert len(parsed.write_columns) == 6
    ev = evaluate(sql, catalog, policy=_allow_policy())
    assert ev.action == "BLOCK"
    assert ev.rule_id == "pii_column"
    assert "email" in ev.reason
    # Must not fail earlier on schema arity ("列数 6 与值个数 5").
    assert "列数" not in (ev.reason or "")
    assert "不一致" not in (ev.reason or "")


def test_upsert_conflict_pii_execute_does_not_change_email(conn, catalog, tmp_path):
    """Under insert=allow, BLOCK still prevents ON CONFLICT email overwrite."""
    before = conn.execute(
        "SELECT email FROM orders WHERE order_id = 1"
    ).fetchone()[0]
    gate = WriteGate(
        db_path=tmp_path / "wh.duckdb",
        conn=conn,
        policy=_allow_policy(),
        catalog=catalog,
        audit_path=tmp_path / "audit.jsonl",
    )
    sql = (
        "INSERT INTO orders (order_id, user_id, amount, dt, status) "
        "VALUES (1, 1001, 12.5, '2026-09-01', 'paid') "
        "ON CONFLICT (order_id) DO UPDATE SET email = 'hacked@evil.com'"
    )
    ev, result = gate.execute(sql)
    after = conn.execute(
        "SELECT email FROM orders WHERE order_id = 1"
    ).fetchone()[0]
    assert ev.action == "BLOCK"
    assert ev.rule_id == "pii_column"
    assert result is None
    assert after == before
    assert after != "hacked@evil.com"


# --- PostgreSQL dialect: DM CTE / SELECT INTO (AST path, no live DB) ----------


def test_pg_data_modifying_cte_not_read_only_allow():
    gate = WriteGate(
        database="postgresql://user:pass@localhost:5432/app",
        policy=_allow_policy(),
    )
    sql = (
        "WITH d AS (DELETE FROM orders WHERE order_id = 1 RETURNING *) "
        "SELECT * FROM d"
    )
    ev = gate.check(sql)
    assert ev.action == "BLOCK"
    assert ev.rule_id == "unsupported_sql"
    assert ev.allowed is False


def test_pg_select_into_not_read_only_allow():
    gate = WriteGate(
        database="postgresql://user:pass@localhost:5432/app",
        policy=_allow_policy(),
    )
    sql = "SELECT order_id INTO orders_backup FROM orders"
    ev = gate.check(sql)
    assert ev.action == "BLOCK"
    assert ev.rule_id == "unsupported_sql"
    assert ev.allowed is False


# --- Blast radius fail-closed + alias -----------------------------------------


def test_update_alias_blast_radius_blocks(conn, catalog):
    """UPDATE ... AS o WHERE o.col must count correctly (not fail-open)."""
    sql = "UPDATE orders AS o SET status='changed' WHERE o.order_id > 0"
    ev = evaluate(sql, catalog, policy=_allow_policy(update_rows=1), conn=conn)
    assert ev.action == "BLOCK"
    assert ev.rule_id == "blast_radius_exceeded"
    assert ev.estimated_rows == 3


def test_update_alias_blast_radius_counts_120(catalog):
    """Acceptance: UPDATE AS o with update_rows=1 BLOCK when COUNT is 120."""
    c = duckdb.connect(":memory:")
    c.execute(ORDERS_DDL)
    vals = ",".join(
        f"({i}, {1000 + i}, 1.0, DATE '2026-09-01', 'a{i}@e.com', '1', 'paid')"
        for i in range(1, 121)
    )
    c.execute(f"INSERT INTO orders VALUES {vals}")
    sql = "UPDATE orders AS o SET status = 'x' WHERE o.order_id > 0"
    ev = evaluate(sql, catalog, policy=_allow_policy(update_rows=1), conn=c)
    c.close()
    assert ev.action == "BLOCK"
    assert ev.rule_id == "blast_radius_exceeded"
    assert ev.estimated_rows == 120


def test_estimate_failure_blocks_not_skip(catalog):
    class BoomConn:
        def execute(self, sql):
            raise RuntimeError("count failed")

    parsed = parse("UPDATE orders SET status='x' WHERE order_id = 1")
    ctx = Context(
        sql=parsed.sql,
        parsed=parsed,
        catalog=catalog,
        policy=_allow_policy(update_rows=100),
        conn=BoomConn(),
        dialect="duckdb",
    )
    result = check_blast_radius(ctx)
    assert result.verdict == "BLOCK"
    assert result.rule_id == "blast_radius_unknown"
    assert "fail closed" in result.reason.lower() or "cannot estimate" in result.reason.lower()


# --- Existing red light preserved ---------------------------------------------


def test_delete_without_where_still_blocked(catalog):
    ev = evaluate("DELETE FROM orders", catalog, policy=_allow_policy())
    assert ev.action == "BLOCK"
    assert ev.rule_id == "delete_without_where"
