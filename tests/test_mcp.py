"""MCP tool tests: no live Claude / Codex / Postgres."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from write_gate.cli import main
from write_gate.mcp_tools import query_sql, write_sql
from write_gate.wrapper import WriteGate

ROOT = Path(__file__).resolve().parents[1]
PG_URL = "postgresql://user:pass@localhost/db"
PAYLOAD_KEYS = {"action", "rule_id", "reason", "operation", "table", "risk"}


def test_write_sql_delete_blocked():
    result = write_sql("DELETE FROM orders")
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"
    assert PAYLOAD_KEYS <= set(result)


def test_query_sql_select_allowed():
    result = query_sql("SELECT 1")
    assert result["action"] == "ALLOW"
    assert result["rule_id"] == "ok"
    assert PAYLOAD_KEYS <= set(result)


def test_query_sql_catalog_select_allowed():
    result = query_sql("SELECT order_id FROM orders LIMIT 1")
    assert result["action"] == "ALLOW"
    assert result["rule_id"] == "ok"


def test_write_sql_postgres_url_blocks_without_live_db():
    result = write_sql("DELETE FROM orders", database=PG_URL)
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"


def test_query_sql_non_read_still_gates():
    result = query_sql("DELETE FROM orders")
    assert result["action"] == "BLOCK"
    assert result["rule_id"] == "delete_without_where"


def test_tools_never_call_execute(monkeypatch):
    def boom(self, sql):
        raise AssertionError(f"execute must not run from MCP tools: {sql}")

    monkeypatch.setattr(WriteGate, "execute", boom)
    assert write_sql("DELETE FROM orders")["action"] == "BLOCK"
    assert query_sql("SELECT 1")["action"] == "ALLOW"


def test_payload_json_serializable():
    blocked = write_sql("DELETE FROM orders")
    allowed = query_sql("SELECT 1")
    json.dumps(blocked)
    json.dumps(allowed)


def test_mcp_example_config_shape():
    data = json.loads((ROOT / "examples/mcp/config.json").read_text())
    server = data["mcpServers"]["sql-write-gate"]
    assert server["command"] == "sql-write-gate"
    assert server["args"] == ["mcp"]


def test_mcp_tools_module_has_no_sdk_import():
    import inspect

    import write_gate.mcp_tools as mod

    src = inspect.getsource(mod)
    assert "from mcp" not in src
    assert "import mcp" not in src


def test_cli_mcp_missing_sdk_prints_hint(monkeypatch, capsys):
    import write_gate.mcp_server as ms

    def boom(**kwargs):
        raise ImportError("No module named mcp")

    monkeypatch.setattr(ms, "run_server", boom)
    rc = main(["mcp"])
    out = capsys.readouterr()
    text = out.err + out.out
    assert rc == 1
    assert 'pip install -e ".[mcp]"' in text


def test_fastmcp_server_exposes_query_and_write_tools():
    pytest.importorskip("mcp.server.fastmcp")
    from write_gate.mcp_server import create_server

    server = create_server()
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "query_sql" in names
    assert "write_sql" in names
