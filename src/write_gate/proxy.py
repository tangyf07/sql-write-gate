"""SQL proxy in front of a real DB. Incoming SQL goes through WriteGate first.

ALLOW then execute. BLOCK and REQUIRE_APPROVAL do not write. Uses
WriteGate.execute only (never raw DuckDB). DuckDB is fully testable without
a Postgres server; postgres:// / postgresql:// URLs take the same path.
"""

from __future__ import annotations

import json
import socket
import sys
from typing import Any, Callable, TextIO

from write_gate.decision import ACTION_ALLOW, ACTION_APPROVAL, ACTION_BLOCK, Decision
from write_gate.wrapper import WriteGate

__all__ = [
    "handle_sql",
    "format_proxy",
    "exit_code",
    "split_statements",
    "parse_listen_addr",
    "serve_listen",
    "handle_connection",
    "run_cli",
]


def handle_sql(gate: WriteGate, sql: str) -> tuple[Decision, Any]:
    """Gate one statement via WriteGate.execute (never raw DuckDB).

    ALLOW executes. BLOCK and REQUIRE_APPROVAL return ``result=None``.
    """
    return gate.execute(sql)


def exit_code(decision: Decision) -> int:
    """Same as ``check``: 0 ALLOW, 1 REQUIRE_APPROVAL, 2 BLOCK."""
    if decision.action == ACTION_ALLOW:
        return 0
    if decision.action == ACTION_APPROVAL:
        return 1
    return 2


def _headline(action: str) -> str:
    if action == ACTION_BLOCK:
        return "BLOCKED"
    if action == ACTION_APPROVAL:
        return "APPROVAL REQUIRED"
    return "ALLOWED"


def _safe(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _executed(decision: Decision, result: Any) -> bool:
    return decision.action == ACTION_ALLOW and result is not None


def format_proxy(decision: Decision, result: Any = None, *, as_json: bool = False) -> str:
    """Human or JSON text for one proxy decision. Always includes executed."""
    executed = _executed(decision, result)
    if as_json:
        payload = decision.to_dict()
        payload["executed"] = executed
        if executed:
            try:
                rows = result.fetchall()
                payload["rows"] = [list(r) for r in rows]
                payload["rowcount"] = len(payload["rows"])
            except Exception:
                payload["rowcount"] = getattr(result, "rowcount", None)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    lines = [
        _headline(decision.action),
        f"Risk: {_safe(decision.risk)}",
        f"Operation: {_safe(decision.operation).upper()}",
        f"Table: {_safe(decision.table)}",
        f"Rule: {_safe(decision.rule_id)}",
        f"Reason: {_safe(decision.reason)}",
        f"executed: {'yes' if executed else 'no'}",
    ]
    return "\n".join(lines)


def split_statements(text: str, *, once: bool = False) -> list[str]:
    """Split stdin into SQL statements: one SQL, or one per non-empty line."""
    raw = (text or "").strip()
    if not raw:
        return []
    if once:
        return [_strip_sql(raw)]
    lines = [_strip_sql(ln) for ln in raw.splitlines()]
    return [ln for ln in lines if ln]


def _strip_sql(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def parse_listen_addr(spec: str) -> tuple[str, int]:
    """Parse ``HOST:PORT`` (port 0 allowed for tests). Bare PORT binds 127.0.0.1."""
    text = (spec or "").strip()
    if not text:
        raise ValueError("listen address is empty")
    if ":" not in text:
        return "127.0.0.1", int(text)
    host, _, port_s = text.rpartition(":")
    return (host or "127.0.0.1"), int(port_s)


def read_sql_from_socket(sock: socket.socket, *, limit: int = 1_000_000) -> str:
    """Read one SQL until newline or semicolon (or EOF / size limit)."""
    buf = bytearray()
    while len(buf) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in chunk or b";" in chunk:
            break
    text = bytes(buf).decode("utf-8", errors="replace")
    for sep in ("\n", ";"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return _strip_sql(text)


def handle_connection(
    conn: socket.socket,
    gate: WriteGate,
    *,
    as_json: bool = False,
) -> Decision:
    """One connection: read one SQL, gate, write BLOCKED/ALLOWED, close is caller."""
    sql = read_sql_from_socket(conn)
    if not sql:
        body = "BLOCKED\nRule: empty_sql\nReason: empty SQL\nexecuted: no\n"
        conn.sendall(body.encode("utf-8"))
        return Decision(
            action=ACTION_BLOCK,
            risk="low",
            rule_id="empty_sql",
            reason="empty SQL",
        )
    decision, result = handle_sql(gate, sql)
    body = format_proxy(decision, result, as_json=as_json) + "\n"
    conn.sendall(body.encode("utf-8"))
    return decision


def serve_listen(
    host: str,
    port: int,
    gate: WriteGate,
    *,
    as_json: bool = False,
    stop: Callable[[], bool] | None = None,
    on_bound: Callable[[str, int], None] | None = None,
    err: TextIO | None = None,
) -> int:
    """Accept connections on HOST:PORT. Each connection is one SQL then close.

    ``port=0`` lets the OS pick an ephemeral port (tests bind 127.0.0.1:0).
    ``stop`` is polled between accepts so tests can shut the server down.
    """
    err = err if err is not None else sys.stderr
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(8)
    sock.settimeout(0.3)
    bound_host, bound_port = sock.getsockname()[:2]
    err.write(f"listening on {bound_host}:{bound_port}\n")
    err.flush()
    if on_bound is not None:
        on_bound(bound_host, bound_port)
    try:
        while True:
            if stop is not None and stop():
                return 0
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if stop is not None and stop():
                    return 0
                raise
            with conn:
                handle_connection(conn, gate, as_json=as_json)
    except KeyboardInterrupt:
        err.write("proxy: listen stopped\n")
        return 0
    finally:
        sock.close()


def _worst_code(codes: list[int]) -> int:
    if not codes:
        return 2
    return max(codes)


def run_statements(
    gate: WriteGate,
    statements: list[str],
    *,
    as_json: bool = False,
    out: TextIO | None = None,
) -> int:
    """Run each SQL through handle_sql. Exit code is the most severe result."""
    out = out if out is not None else sys.stdout
    codes: list[int] = []
    for sql in statements:
        decision, result = handle_sql(gate, sql)
        out.write(format_proxy(decision, result, as_json=as_json) + "\n")
        out.flush()
        codes.append(exit_code(decision))
    return _worst_code(codes)


def run_cli(
    gate: WriteGate,
    *,
    sql: str | None = None,
    listen: str | None = None,
    once: bool = False,
    as_json: bool = False,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    stop: Callable[[], bool] | None = None,
    on_bound: Callable[[str, int], None] | None = None,
) -> int:
    """CLI body for ``sql-write-gate proxy``. One-shot --sql / stdin EOF / listen."""
    err = err if err is not None else sys.stderr
    stdin = stdin if stdin is not None else sys.stdin
    if listen:
        host, port = parse_listen_addr(listen)
        return serve_listen(
            host,
            port,
            gate,
            as_json=as_json,
            stop=stop,
            on_bound=on_bound,
            err=err,
        )

    once = bool(once) or bool(sql and str(sql).strip())
    statements: list[str] = []
    if sql and str(sql).strip():
        statements = [_strip_sql(str(sql))]
    else:
        isatty = getattr(stdin, "isatty", lambda: False)
        if callable(isatty) and isatty():
            err.write("proxy: provide --sql, pipe SQL on stdin, or --listen HOST:PORT\n")
            return 2
        statements = split_statements(stdin.read(), once=once)

    if not statements:
        err.write("proxy: empty SQL\n")
        return 2
    return run_statements(gate, statements, as_json=as_json, out=out)
