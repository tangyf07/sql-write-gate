"""PreToolUse hook: agents cannot talk to the DB via raw psql / other DB CLIs.

The hook never executes user SQL. Extracted statements go through WriteGate.check
only. Interactive or unparseable DB shells are refused.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from write_gate.decision import ACTION_ALLOW, ACTION_APPROVAL, RULE_RAW_DB_CLI, Decision
from write_gate.wrapper import WriteGate

DB_CLIS = ("psql", "mysql", "mysqlsh", "duckdb", "sqlite3")

_SQL_FLAGS: dict[str, tuple[str, ...]] = {
    "psql": ("-c", "--command"),
    "mysql": ("-e", "--execute"),
    "mysqlsh": ("-e", "--execute"),
    "duckdb": ("-c", "--cmd", "-s", "--sql"),
    "sqlite3": ("-cmd",),
}

_WRAPPERS = {"sudo", "env", "command", "nohup", "time", "nice", "stdbuf", "timeout"}

_SHELL_OPS = {"&&", "||", "|", ";", "&"}

_DB_CLI_WORD = re.compile(
    r"(?:^|[^\w.-])(psql|mysqlsh|mysql|duckdb|sqlite3)(?:$|[^\w.-])",
    re.IGNORECASE,
)

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_SQL_HEAD = re.compile(
    r"^(SELECT|INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|WITH|COPY|"
    r"REPLACE|MERGE|CALL|DO|BEGIN|EXPLAIN|VACUUM|ANALYZE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Segment:
    cli: str | None
    sqls: tuple[str, ...]
    raw: bool


def command_from_payload(payload: Any) -> str | None:
    """Pull the bash command out of a Claude / Codex PreToolUse JSON object."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "sql"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    elif isinstance(tool_input, str) and tool_input.strip():
        return tool_input
    for key in ("command", "cmd"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def parse_stdin_payload(text: str) -> Any | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _basename(token: str) -> str:
    name = Path(token).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _is_sql_flag(cli: str, token: str) -> bool:
    return token in _SQL_FLAGS.get(cli, ())


def _looks_like_sql(text: str) -> bool:
    return bool(_SQL_HEAD.match(text.strip().lstrip("(")))


def _skip_wrappers(tokens: list[str]) -> list[str]:
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if _ENV_ASSIGN.match(t):
            i += 1
            continue
        name = _basename(t)
        if name in _WRAPPERS:
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 1
            # timeout DURATION cmd
            if name == "timeout" and i < n and not tokens[i].startswith("-"):
                i += 1
            continue
        break
    return tokens[i:]


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for t in tokens:
        if t in _SHELL_OPS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(t)
    if current:
        segments.append(current)
    return segments


def _collect_sql_tokens(tokens: list[str], start: int) -> tuple[list[str], int]:
    if start >= len(tokens) or tokens[start].startswith("-"):
        return [], start
    first = tokens[start]
    if any(ch.isspace() for ch in first):
        return [first], start + 1
    parts: list[str] = []
    i = start
    while i < len(tokens) and not tokens[i].startswith("-"):
        parts.append(tokens[i])
        i += 1
    return parts, i


def _extract_from_cli(cli: str, tokens: list[str]) -> tuple[list[str], bool]:
    """Return (sql_strings, saw_sql_flag_without_value)."""
    sqls: list[str] = []
    incomplete_flag = False
    positionals: list[str] = []
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--") and "=" in t:
            flag, _, value = t.partition("=")
            if _is_sql_flag(cli, flag):
                extra, i = _collect_sql_tokens(tokens, i + 1)
                joined = " ".join([value, *extra]).strip() if extra else value.strip()
                if joined:
                    sqls.append(joined)
                else:
                    incomplete_flag = True
                continue
            i += 1
            continue
        if _is_sql_flag(cli, t):
            extra, i = _collect_sql_tokens(tokens, i + 1)
            if extra:
                sqls.append(" ".join(extra).strip())
            else:
                incomplete_flag = True
            continue
        if t.startswith("-"):
            i += 1
            if i < len(tokens) and not tokens[i].startswith("-"):
                i += 1
            continue
        positionals.append(t)
        i += 1

    if sqls:
        return sqls, incomplete_flag
    if incomplete_flag:
        return [], True
    if cli in {"duckdb", "sqlite3"}:
        if len(positionals) >= 2:
            return [positionals[1]], False
        if len(positionals) == 1 and _looks_like_sql(positionals[0]):
            return [positionals[0]], False
    return [], False


def inspect_bash(command: str) -> list[_Segment]:
    """Inspect a bash command for DB CLIs. Empty list means not a DB CLI."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        match = _DB_CLI_WORD.search(command)
        if match:
            return [_Segment(cli=match.group(1).lower(), sqls=(), raw=True)]
        return []

    found: list[_Segment] = []
    for raw_seg in _split_segments(tokens):
        stripped = _skip_wrappers(raw_seg)
        if not stripped:
            continue
        cli = _basename(stripped[0])
        if cli not in DB_CLIS:
            continue
        sqls, incomplete = _extract_from_cli(cli, stripped)
        if sqls and not incomplete:
            found.append(_Segment(cli=cli, sqls=tuple(sqls), raw=False))
        else:
            found.append(_Segment(cli=cli, sqls=(), raw=True))
    return found


def _emit_refuse(
    stream: TextIO,
    *,
    rule_id: str,
    reason: str,
    extra: str | None = None,
) -> None:
    stream.write("BLOCKED\n")
    stream.write(f"Rule: {rule_id}\n")
    stream.write(f"Reason: {reason}\n")
    if extra:
        stream.write(extra.rstrip() + "\n")


def _emit_decision(stream: TextIO, decision: Decision) -> None:
    extra = None
    if decision.action == ACTION_APPROVAL:
        extra = (
            "REQUIRE_APPROVAL is refused by the hook so agents cannot silently write. "
            "Use sql-write-gate check|exec with a human in the loop."
        )
    _emit_refuse(
        stream,
        rule_id=decision.rule_id,
        reason=decision.reason,
        extra=extra,
    )


def _raw_cli_reason(cli: str) -> str:
    return (
        f"Raw {cli} is blocked (interactive or unparseable; no -c/--command SQL). "
        "Use sql-write-gate check|exec. Do not open a raw DB shell."
    )


def _gate(
    *,
    database: str | None,
    db: str | None,
    catalog: str | None,
    policy: str | None,
    agent: str,
) -> WriteGate:
    return WriteGate(
        db_path=Path(db) if db else None,
        database=database,
        catalog_path=Path(catalog) if catalog else None,
        policy_path=Path(policy) if policy else None,
        agent=agent,
    )


def _check_sql(
    sql: str,
    *,
    database: str | None,
    db: str | None,
    catalog: str | None,
    policy: str | None,
    agent: str,
    err: TextIO,
) -> int:
    with _gate(
        database=database,
        db=db,
        catalog=catalog,
        policy=policy,
        agent=agent,
    ) as gate:
        decision = gate.check(sql)
    if decision.action == ACTION_ALLOW:
        return 0
    _emit_decision(err, decision)
    return 2


def run_hook(
    *,
    bash_command: str | None = None,
    sql: str | None = None,
    stdin_text: str | None = None,
    database: str | None = None,
    db: str | None = None,
    catalog: str | None = None,
    policy: str | None = None,
    agent: str = "hook",
    err: TextIO | None = None,
) -> int:
    """Evaluate a PreToolUse payload or test flags. Never executes user SQL.

    Returns 0 (allow / not a DB CLI) or 2 (BLOCK / REQUIRE_APPROVAL / raw CLI).
    """
    err = err if err is not None else sys.stderr

    if sql and sql.strip():
        return _check_sql(
            sql,
            database=database,
            db=db,
            catalog=catalog,
            policy=policy,
            agent=agent,
            err=err,
        )

    command = bash_command
    if not command:
        payload = parse_stdin_payload(stdin_text or "")
        command = command_from_payload(payload) if payload is not None else None

    if not command or not str(command).strip():
        return 0

    segments = inspect_bash(command)
    if not segments:
        return 0

    for seg in segments:
        if seg.raw or not seg.sqls:
            _emit_refuse(
                err,
                rule_id=RULE_RAW_DB_CLI,
                reason=_raw_cli_reason(seg.cli or "db-cli"),
            )
            return 2
        for statement in seg.sqls:
            rc = _check_sql(
                statement,
                database=database,
                db=db,
                catalog=catalog,
                policy=policy,
                agent=agent,
                err=err,
            )
            if rc != 0:
                return rc
    return 0
