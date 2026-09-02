"""Proxy CLI tests: DuckDB tmp warehouse. No live Postgres required."""

from __future__ import annotations

import inspect
import io
import socket
import threading
from pathlib import Path

import duckdb

from write_gate.cases import LEGAL_WRITE_SQL
from write_gate.cli import main
from write_gate.db import ORDERS_DDL
from write_gate.paths import DEMO_POLICY_PATH, EXAMPLES_POLICY_PATH
from write_gate.proxy import (
    handle_connection,
    handle_sql,
    parse_listen_addr,
    serve_listen,
)
from write_gate.wrapper import WriteGate

PG_URL = "postgresql://user:pass@localhost/db"


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


def test_proxy_sql_legal_write_allow_then_select_finds_row(tmp_path, capsys):
    db_path = _seed_warehouse(tmp_path)
    rc = main(
        [
            "proxy",
            "--database",
            str(db_path),
            "--policy",
            str(DEMO_POLICY_PATH),
            "--sql",
            LEGAL_WRITE_SQL,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "ALLOWED" in out
    assert "executed: yes" in out
    n = _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001")
    assert n == 1

    rc2 = main(
        [
            "proxy",
            "--database",
            str(db_path),
            "--policy",
            str(DEMO_POLICY_PATH),
            "--sql",
            "SELECT order_id FROM orders WHERE order_id = 900001",
        ]
    )
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "ALLOWED" in out2


def test_proxy_sql_delete_blocked_rows_unchanged(tmp_path, capsys):
    db_path = _seed_warehouse(tmp_path)
    before = _count(db_path)
    rc = main(
        [
            "proxy",
            "--database",
            str(db_path),
            "--sql",
            "DELETE FROM orders",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out
    assert "executed: no" in out
    assert _count(db_path) == before


def test_proxy_production_insert_approval_no_row(tmp_path, capsys):
    db_path = _seed_warehouse(tmp_path)
    rc = main(
        [
            "proxy",
            "--database",
            str(db_path),
            "--policy",
            str(EXAMPLES_POLICY_PATH),
            "--sql",
            LEGAL_WRITE_SQL,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "APPROVAL" in out
    assert "executed: no" in out
    n = _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001")
    assert n == 0


def test_proxy_postgres_url_delete_block_without_live_db(capsys):
    rc = main(
        [
            "proxy",
            "--database",
            PG_URL,
            "--sql",
            "DELETE FROM orders",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out
    assert "executed: no" in out


def test_handle_sql_uses_writegate_execute(tmp_path, monkeypatch):
    db_path = _seed_warehouse(tmp_path)
    seen: list[str] = []
    real = WriteGate.execute

    def wrapped(self, sql):
        seen.append(sql)
        return real(self, sql)

    monkeypatch.setattr(WriteGate, "execute", wrapped)
    with WriteGate(db_path=db_path, policy_path=DEMO_POLICY_PATH) as gate:
        decision, result = handle_sql(gate, "DELETE FROM orders")
    assert decision.action == "BLOCK"
    assert decision.rule_id == "delete_without_where"
    assert result is None
    assert seen == ["DELETE FROM orders"]


def test_proxy_module_has_no_raw_duckdb_import():
    import write_gate.proxy as mod

    src = inspect.getsource(mod)
    assert "import duckdb" not in src
    assert "from duckdb" not in src


def test_proxy_stdin_once_delete(tmp_path, monkeypatch, capsys):
    db_path = _seed_warehouse(tmp_path)
    before = _count(db_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("DELETE FROM orders\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = main(["proxy", "--database", str(db_path), "--once"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out
    assert _count(db_path) == before


def test_proxy_stdin_batch_eof_allow_then_block(tmp_path, monkeypatch, capsys):
    db_path = _seed_warehouse(tmp_path)
    stdin = LEGAL_WRITE_SQL + "\nDELETE FROM orders\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = main(
        [
            "proxy",
            "--database",
            str(db_path),
            "--policy",
            str(DEMO_POLICY_PATH),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 2
    assert "ALLOWED" in out
    assert "BLOCKED" in out
    assert "delete_without_where" in out
    assert _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001") == 1
    assert _count(db_path) == 3


def test_parse_listen_addr():
    assert parse_listen_addr("127.0.0.1:0") == ("127.0.0.1", 0)
    assert parse_listen_addr("8765") == ("127.0.0.1", 8765)
    assert parse_listen_addr(":0") == ("127.0.0.1", 0)


def test_listen_handle_connection_delete_block(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    server, client = socket.socketpair()
    try:
        with WriteGate(db_path=db_path, policy_path=DEMO_POLICY_PATH) as gate:
            client.sendall(b"DELETE FROM orders\n")
            decision = handle_connection(server, gate)
        data = client.recv(4096).decode("utf-8")
    finally:
        server.close()
        client.close()
    assert decision.action == "BLOCK"
    assert decision.rule_id == "delete_without_where"
    assert "BLOCKED" in data
    assert "delete_without_where" in data
    assert _count(db_path) == 2


def test_listen_bind_ephemeral_delete_and_insert(tmp_path):
    db_path = _seed_warehouse(tmp_path)
    bound: dict[str, int | str] = {}
    ready = threading.Event()
    stop_flag = threading.Event()

    def on_bound(host: str, port: int) -> None:
        bound["host"] = host
        bound["port"] = port
        ready.set()

    def run() -> None:
        with WriteGate(db_path=db_path, policy_path=DEMO_POLICY_PATH) as gate:
            serve_listen(
                "127.0.0.1",
                0,
                gate,
                stop=stop_flag.is_set,
                on_bound=on_bound,
            )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(2)
    host = str(bound["host"])
    port = int(bound["port"])
    assert port > 0

    def _talk(sql: str) -> str:
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall((sql + "\n").encode("utf-8"))
            return sock.recv(4096).decode("utf-8")

    blocked = _talk("DELETE FROM orders")
    allowed = _talk(LEGAL_WRITE_SQL)
    stop_flag.set()
    thread.join(timeout=2)

    assert "BLOCKED" in blocked
    assert "delete_without_where" in blocked
    assert "ALLOWED" in allowed
    assert "executed: yes" in allowed
    assert _count(db_path, "SELECT COUNT(*) FROM orders WHERE order_id = 900001") == 1
    assert _count(db_path) == 3


def test_proxy_db_flag_alias(tmp_path, capsys):
    db_path = _seed_warehouse(tmp_path)
    rc = main(
        [
            "proxy",
            "--db",
            str(db_path),
            "--sql",
            "DELETE FROM orders",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out
