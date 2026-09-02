"""PreToolUse hook tests: no live Claude / Codex / Postgres."""

from __future__ import annotations

import io
import json
from pathlib import Path

from write_gate.cli import main
from write_gate.hooks import inspect_bash, run_hook
from write_gate.wrapper import WriteGate

ROOT = Path(__file__).resolve().parents[1]
PG_URL = "postgresql://user:pass@localhost/db"

DELETE_PSQL = "psql -c DELETE FROM orders"
PRETOOLUSE_DELETE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": DELETE_PSQL},
}


def _run_hook_argv(argv, *, stdin_text=None, monkeypatch=None):
    if stdin_text is not None:
        assert monkeypatch is not None
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return main(["hook", *argv])


def _combined(capsys) -> str:
    captured = capsys.readouterr()
    return captured.err + captured.out


def test_stdin_pretooluse_psql_delete_blocked(capsys, monkeypatch):
    rc = _run_hook_argv(
        [],
        stdin_text=json.dumps(PRETOOLUSE_DELETE),
        monkeypatch=monkeypatch,
    )
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out


def test_command_flag_psql_delete_blocked(capsys):
    rc = main(["hook", "--command", DELETE_PSQL])
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out


def test_sql_flag_delete_blocked(capsys):
    rc = main(["hook", "--sql", "DELETE FROM orders"])
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out


def test_database_postgres_url_blocks_without_live_db(capsys):
    rc = main(
        [
            "hook",
            "--database",
            PG_URL,
            "--command",
            DELETE_PSQL,
        ]
    )
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "delete_without_where" in out


def test_harmless_ls_pretooluse_allows_quietly(capsys, monkeypatch):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -l"},
    }
    rc = _run_hook_argv(
        [],
        stdin_text=json.dumps(payload),
        monkeypatch=monkeypatch,
    )
    out = _combined(capsys)
    assert rc == 0
    assert out == ""


def test_pytest_command_allows_quietly(capsys):
    rc = main(["hook", "--command", "pytest -q"])
    out = _combined(capsys)
    assert rc == 0
    assert out == ""


def test_interactive_psql_blocked(capsys):
    rc = main(["hook", "--command", "psql"])
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "raw_db_cli" in out
    assert "sql-write-gate check" in out


def test_psql_no_c_with_url_blocked(capsys):
    rc = main(["hook", "--command", "psql postgresql://user:pass@localhost/db"])
    out = _combined(capsys)
    assert rc == 2
    assert "raw_db_cli" in out


def test_mysql_execute_delete_blocked(capsys):
    rc = main(["hook", "--command", "mysql -e DELETE FROM orders"])
    out = _combined(capsys)
    assert rc == 2
    assert "delete_without_where" in out


def test_quoted_psql_c_delete_blocked(capsys):
    rc = main(["hook", "--command", "psql -c DELETE FROM orders"])
    out = _combined(capsys)
    assert rc == 2
    assert "delete_without_where" in out


def test_select_via_psql_allowed_quiet(capsys):
    rc = main(["hook", "--command", "psql -c SELECT 1"])
    out = _combined(capsys)
    assert rc == 0
    assert out == ""


def test_insert_require_approval_refused(capsys):
    sql = (
        "INSERT INTO orders (order_id, user_id, amount, dt, status) "
        "VALUES (900001, 42, 18.50, '2026-09-01', 'paid')"
    )
    rc = main(["hook", "--sql", sql])
    out = _combined(capsys)
    assert rc == 2
    assert "BLOCKED" in out
    assert "environment_policy" in out


def test_hook_never_executes_user_sql(monkeypatch, capsys):
    def boom(self, sql):
        raise AssertionError(f"execute must not run from hook: {sql}")

    monkeypatch.setattr(WriteGate, "execute", boom)
    rc = main(["hook", "--command", DELETE_PSQL])
    assert rc == 2
    assert "delete_without_where" in _combined(capsys)


def test_duckdb_cli_delete_blocked(capsys):
    rc = main(["hook", "--command", "duckdb warehouse.duckdb -c DELETE FROM orders"])
    out = _combined(capsys)
    assert rc == 2
    assert "delete_without_where" in out


def test_sqlite3_positional_delete_blocked(capsys):
    rc = main(["hook", "--command", 'sqlite3 warehouse.db "DELETE FROM orders"'])
    out = _combined(capsys)
    assert rc == 2
    assert "delete_without_where" in out


def test_inspect_bash_ignores_echo_psql():
    assert inspect_bash("echo psql -c DELETE FROM orders") == []


def test_claude_example_settings_shape():
    data = json.loads((ROOT / "examples/claude/settings.json").read_text())
    assert "hooks" in data
    group = data["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "Bash"
    hook = group["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"] == "sql-write-gate hook"


def test_codex_example_hooks_shape():
    data = json.loads((ROOT / "examples/codex/hooks.json").read_text())
    assert "hooks" not in data
    group = data["PreToolUse"][0]
    assert group["matcher"] == "Bash"
    hook = group["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"] == "sql-write-gate hook"


def test_run_hook_empty_is_quiet():
    buf = io.StringIO()
    rc = run_hook(stdin_text="", err=buf)
    assert rc == 0
    assert buf.getvalue() == ""
