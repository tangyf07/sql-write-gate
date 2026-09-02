# sql-write-gate

写库前门禁 · Policy firewall for AI agents writing to databases.

Prevent Claude Code, Codex, Cursor and MCP agents from executing unsafe database operations.

```
  Agent SQL  ─►  sql-write-gate  ─►  ALLOW / BLOCK / APPROVAL  ─►  Database
```

Deterministic policy engine (sqlglot AST + catalog + policy.yaml). **No LLM. No API key.**

```bash
pip install -e .
sql-write-gate check "DELETE FROM users"
```

```
BLOCKED
Risk: critical
Operation: DELETE
Table: users
Rule: delete_without_where
Reason: DELETE without a WHERE clause is forbidden (full-table delete on users)
```

```bash
sql-write-gate check "DELETE FROM orders"
# → BLOCKED  rule=delete_without_where   (no API key)
```

## What it blocks

- [x] `DROP TABLE` / `TRUNCATE` / `ALTER TABLE`
- [x] `DELETE` / `UPDATE` without `WHERE`
- [x] Blast-radius: estimated rows over `update_rows` / `delete_rows`
- [x] Schema: unknown table/column, type mismatch
- [x] PII writes blocked; `SELECT` of PII columns requires approval
- [x] Environment policy: per-operation allow / block / approval
- [x] Freshness: expired partitions (`dt` before cutoff)
- [x] JSONL audit log (`.logs/audit.jsonl`)
- [x] Deterministic rules — no LLM, no network

## 30-second path

```bash
cd sql-write-gate
pip install -e .          # or: make install
sql-write-gate check "DELETE FROM orders"
# BLOCKED / delete_without_where — no API key required

make demo                 # three write cases (uses examples/policy.demo.yaml)
make test                 # pytest -q
```

`python -m write_gate check "DELETE FROM orders"` works the same.

## v0.2 — PostgreSQL adapter

DuckDB is still the default. Pass a URL to use Postgres; all v0.1 guards apply on both adapters.

```python
from write_gate import WriteGate

gate = WriteGate(database="postgresql://user:pass@localhost:5432/app")
decision, result = gate.execute("DELETE FROM orders")
# BLOCKED / delete_without_where  (AST path; no live `orders` table required)
```

```bash
sql-write-gate check --database "$DATABASE_URL" "DELETE FROM orders"
# or: export DATABASE_URL=postgresql://...
```

`postgres://` and `postgresql://` select Postgres; anything else is a DuckDB file path. `WriteGate(database=...)` / `database_url=` / env `DATABASE_URL` are equivalent.

Blast-radius on Postgres uses `SELECT COUNT(*) ... WHERE <predicate>` (same `update_rows` / `delete_rows` limits as DuckDB). Optional driver: `pip install -e ".[postgres]"`. Default `pip install -e .` stays DuckDB-only.

The 30-second path above is unchanged.

## v0.3 — Claude Code / Codex PreToolUse hook

Agents cannot talk to the DB via raw `psql` (also `mysql`, `mysqlsh`, `duckdb`, `sqlite3`). They must go through `sql-write-gate`. The hook never executes SQL; raw `psql` is never the write path.

Copy into the project `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "sql-write-gate hook"
          }
        ]
      }
    ]
  }
}
```

Copy into `.codex/hooks.json` (PreToolUse at root — no wrapping `hooks` key):

```json
{
  "PreToolUse": [
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "sql-write-gate hook"
        }
      ]
    }
  ]
}
```

30-second no-IDE demo (no LLM, no API key):

```bash
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"psql -c DELETE FROM orders"}}' \
  | sql-write-gate hook
# exit 2 / BLOCKED / delete_without_where
```

Non-SQL bash (`ls`, `pytest`) is allowed and stays quiet. Interactive `psql` (no `-c`) is blocked: use `sql-write-gate check|exec`. `REQUIRE_APPROVAL` is also refused (exit 2) so agents cannot silently write in production.

DuckDB 30-second path (`sql-write-gate check "DELETE FROM orders"` / `make demo`) is unchanged.

## Policy

Default (`policy.yaml` / `examples/policy.yaml`) is **production**:

| operation | rule |
|-----------|------|
| select | allow |
| insert | approval |
| update | approval |
| delete | block |
| ddl | block |

Limits: `update_rows: 100`, `delete_rows: 50`.

`make demo` three INSERT cases pass `--policy examples/policy.demo.yaml` (insert=allow) so a legal write can still show **ALLOW**. CLI / README screenshots use production policy.

```bash
sql-write-gate check --policy examples/policy.yaml "UPDATE orders SET status='expired' WHERE id=123"
sql-write-gate check "SELECT id, name FROM users LIMIT 10"
sql-write-gate audit
```

## Decision model

`ALLOW` | `BLOCK` | `REQUIRE_APPROVAL` with `risk` `low|medium|critical`, `rule_id`, `reason`, `evidence`.

Guards (any **BLOCK** wins, else any **APPROVAL**, else **ALLOW**):

`destructive` → `schema` → `pii` → `freshness` → `blast_radius` → `environment`

## 三条用例 (`make demo`)

Dates anchored `as_of=2026-09-02`; partitions older than 7 days (`dt < 2026-08-26`) are expired.

| # | 场景 | 期望 | `rule_id` |
|---|------|------|-----------|
| 1 | 合法写入：新鲜分区 `dt='2026-09-01'`，只写 `order_id,user_id,amount,dt,status` | ALLOWED | `ok` |
| 2 | 过期分区：`dt='2026-08-01'` | BLOCKED | `expired_partition` |
| 3 | PII 写入：INSERT 带 `email` | BLOCKED | `pii_column` |

示例表 `orders` 列：`order_id, user_id, amount, dt, email, phone, status`。种子约 120 行。

**唯一写入口**：`WriteGate.execute(sql)`。脚本与测试不得绕过 wrapper 直接调用 DuckDB 写 API（种子脚本 `scripts/gen_seed.py` 除外）。

## Catalog / PII

`seed/catalog.json` (copied at `examples/catalog.json`): writable tables, allowed columns, `pii_columns`, optional `restricted_columns` (`id_card`, `card_number`).

- Write to PII / restricted columns → **BLOCK**
- `SELECT` of PII columns → **REQUIRE_APPROVAL** (not silent allow)
- `SELECT` of restricted columns → **BLOCK**

## Audit

Every `check` / `exec` appends a JSON line to `.logs/audit.jsonl`:

`timestamp, agent, environment, sql, operation, table, estimated_rows, decision, rule_id`

```bash
sql-write-gate audit
```

## 非目标

- 企业级 DQ / 数据质量平台、血缘 lineage
- ChatBI、SSO、多租户、计费
- LangGraph / CrewAI / 远程 MCP / 在线模型 / Web UI / PyPI publish
- spark-retail-dw 克隆、Spark 数仓、海量数据

Local, deterministic, screenshot-ready. DuckDB by default; Postgres via URL.

## 许可

MIT。见 [LICENSE](LICENSE).
