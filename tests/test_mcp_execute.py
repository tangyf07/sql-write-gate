"""MCP execute-on-ALLOW: DuckDB persist + fake PG. No live server required."""

from __future__ import annotations

from pathlib import Path

import duckdb

from write_gate.cases import LEGAL_WRITE_SQL
from write_gate.db import ORDERS_DDL
from write_gate.mcp_tools import query_sql, write_sql
from write_gate.paths import DEMO_POLICY_PATH, EXAMPLES_POLICY_PATH
from write_gate.wrapper import WriteGate

PG_URL = "postgresql://user:pass@localhost/db"


class FakePg:
    """DuckDB-like execute/fetchone surface used as a fake PG backend."""

    def __init__(self, count: int = 3) -> None:
        self.count = count
        self.statements: list[str] = []

    def execute(self, sql: str):
        self.statements.append(sql)
        return self

    def fetchone(self):
        return (self.count,)

    def fetchall(self):
        return []

    def fetchmany(self, size: int = 50):
        del size
        return []

    def close(self) -> None:
        pass


def _seed_warehouse(tmp_path) -> Path:
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(ORDERS_DDL)
    conn.execute(
        "INSERT INTO orders VALUES "
        "(1, 1001, 12.5, DATE '2026-09-01', 'a@example.com', '13800000001', 'paid'),"
        "(2, 1002, 9.9, DATE '2026-08-01', 'b@example.com', '13800000002', 'pending')"
    )
    conn.close()
    return db_path


def _count(db_path: Path, sql: str = "SELECT COUNT(*) FROM orders") -> int:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def test_allow_insert_persists_and_select_finds_row(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    result = write_sql(
        LEGAL_WRITE_SQL,
        db_path=db_path,
        policy_path=DEMO_POLICY_PATH,
    )
    assert result["action"] == "ALLOW"
    assert result["executed"] is True
    found = query_sql(
        "SELECT order_id FROM orders WHERE order_id = 900001",
        db_path=db_path,
        policy_path=DEMO_POLICY_PATH,
    )
    assert found["action"] == "ALLOW"
    assert found["executed"] is True
    assert found.get("rows")
    assert any(row[0] == 900001 for row in found["rows"])
    n = _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001")
    assert n == 1


def test_delete_without_where_does_not_drop_rows(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    before = _count(db_path)
    result = write_sql("DELETE FROM orders", db_path=db_path)
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"
    assert result["executed"] is False
    assert _count(db_path) == before


def test_production_insert_approval_does_not_land(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    result = write_sql(
        LEGAL_WRITE_SQL,
        db_path=db_path,
        policy_path=EXAMPLES_POLICY_PATH,
    )
    assert result["action"] == "REQUIRE_APPROVAL"
    assert result["executed"] is False
    n = _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001")
    assert n == 0


def test_query_sql_block_does_not_include_rows(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    result = query_sql("DELETE FROM orders", db_path=db_path)
    assert result["action"] == "BLOCK"
    assert result["executed"] is False
    assert "rows" not in result


def test_write_sql_delete_still_block_default_policy():
    result = write_sql("DELETE FROM orders")
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"
    assert result["executed"] is False


def test_query_sql_select_one_allow_includes_rows():
    result = query_sql("SELECT 1")
    assert result["action"] == "ALLOW"
    assert result["executed"] is True
    assert result.get("rows") == [[1]]


def test_write_sql_allow_insert_fake_pg(monkeypatch, tmp_path):
    fake = FakePg(count=0)

    def fake_connect(dsn, **kwargs):
        del kwargs
        assert str(dsn).startswith("postgres")
        return fake

    monkeypatch.setattr("write_gate.adapters.postgres.connect", fake_connect)
    result = write_sql(
        LEGAL_WRITE_SQL,
        database=PG_URL,
        policy_path=DEMO_POLICY_PATH,
    )
    assert result["action"] == "ALLOW"
    assert result["executed"] is True
    assert any("INSERT" in s.upper() for s in fake.statements)


def test_write_sql_delete_fake_pg_does_not_send_delete(monkeypatch):
    fake = FakePg(count=99)

    def fake_connect(dsn, **kwargs):
        del kwargs
        return fake

    monkeypatch.setattr("write_gate.adapters.postgres.connect", fake_connect)
    result = write_sql("DELETE FROM orders", database=PG_URL)
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"
    assert result["executed"] is False
    assert not any(s.strip().upper().startswith("DELETE") for s in fake.statements)


def test_query_sql_block_fake_pg_does_not_run_user_sql(monkeypatch):
    fake = FakePg(count=99)
    monkeypatch.setattr(
        "write_gate.adapters.postgres.connect",
        lambda dsn, **kwargs: fake,
    )
    result = query_sql("DELETE FROM orders", database=PG_URL)
    assert result["action"] == "BLOCK"
    assert result["executed"] is False
    assert not any(s.strip().upper().startswith("DELETE") for s in fake.statements)


def test_production_insert_fake_pg_does_not_execute(monkeypatch):
    fake = FakePg(count=0)
    monkeypatch.setattr(
        "write_gate.adapters.postgres.connect",
        lambda dsn, **kwargs: fake,
    )
    result = write_sql(
        LEGAL_WRITE_SQL,
        database=PG_URL,
        policy_path=EXAMPLES_POLICY_PATH,
    )
    assert result["action"] == "REQUIRE_APPROVAL"
    assert result["executed"] is False
    assert not any("INSERT" in s.upper() for s in fake.statements)


def test_tools_call_execute(monkeypatch):
    seen: list[str] = []
    real = WriteGate.execute

    def wrapped(self, sql):
        seen.append(sql)
        return real(self, sql)

    monkeypatch.setattr(WriteGate, "execute", wrapped)
    blocked = write_sql("DELETE FROM orders")
    allowed = query_sql("SELECT 1")
    assert blocked["action"] == "BLOCK"
    assert allowed["action"] == "ALLOW"
    assert seen
