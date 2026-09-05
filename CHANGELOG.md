# Changelog

All notable changes to **sql-write-gate** are documented here.

## [0.14.0] — 2026-09-05

### Changed

- README top badges: CI, Release, Python 3.11+, MIT License
- GitHub Release for tag `v0.13.0` (docs/release polish; product behavior unchanged from 0.13.0)

## [0.13.0] — 2026-09-05

### Added

- SQLite adapter for `sqlite:///` and `sqlite+aiosqlite://` (file-path form; sqlglot dialect `sqlite`)
- Uses stdlib `sqlite3` (no new required dependency)
- AST guards (e.g. `DELETE` without `WHERE`) still BLOCK without a live SQLite DB / tables

### Notes

- DuckDB / Postgres / MySQL / hook / MCP / CI unchanged
- No Web UI. No PyPI publish.

## [0.11.0] — 2026-09-05

### Added

- GitHub Actions CI (`.github/workflows/ci.yml`): on push/PR to `main`, runs `pip install -e ".[dev]"` then `make test`
- `make test` now depends on `seed` so CI has warehouse tables without a manual seed step

### Notes

- No product behavior change vs 0.10
- Green run: https://github.com/tangyf07/sql-write-gate/actions/runs/33945916950

## [0.10.0] — 2026-09-05

### Added

- MySQL adapter for `mysql://` and `mysql+pymysql://` (sqlglot dialect `mysql`)
- Optional extra: `pip install -e ".[mysql]"` (`pymysql`)
- AST guards (e.g. `DELETE` without `WHERE`) still BLOCK without a live MySQL

## [0.9.0] — 2026-09-02

### Changed

- README first screen lists six CLI commands: `check`, `hook`, `mcp`, `proxy`, `approve`, `audit`
- `make demo` walks those commands after the three DuckDB write cases
- Version polish only; guards unchanged

## [0.8.0] — 2026-09-02

### Added

- Human-readable `sql-write-gate audit` table: TIME / SOURCE / OP / TABLE / VERDICT
- JSONL audit file format unchanged (`.logs/audit.jsonl`)

## [0.7.0] — 2026-09-02

### Added

- Real approval queue (`.logs/approvals.jsonl`): `REQUIRE_APPROVAL` recorded, not executed
- CLI: `approve <id>`, `reject <id>`, `pending`

## [0.6.0] — 2026-09-02

### Added

- `sql-write-gate proxy`: gate then execute if ALLOW; BLOCK / APPROVAL do not write
- One-shot `--sql` / stdin; optional `--listen` text protocol

## [0.5.0] — 2026-09-02

### Changed

- MCP `write_sql` / `query_sql` call `WriteGate.execute`: ALLOW persists; BLOCK / APPROVAL do not

## [0.4.0] — 2026-09-02

### Added

- MCP stdio server: `sql-write-gate mcp` with `query_sql` / `write_sql` (check-only at intro)
- Optional extra: `pip install -e ".[mcp]"`

## [0.3.0] — 2026-09-02

### Added

- PreToolUse hook: `sql-write-gate hook` blocks raw `psql` / `mysql` / `duckdb` / `sqlite3`
- Example Claude Code / Codex hook configs

## [0.2.0] — 2026-09-02

### Added

- PostgreSQL adapter for `postgres://` / `postgresql://`
- Blast radius via `COUNT(*)` on Postgres
- Optional extra: `pip install -e ".[postgres]"`

## [0.1.0] — 2026-09-02

### Added

- Deterministic write-gate (sqlglot AST + catalog + `policy.yaml`): no LLM, no API key
- DuckDB default warehouse
- Guards: destructive, schema, PII, freshness, blast radius, environment
- CLI: `sql-write-gate check` (+ early exec/audit paths)
- Decision model: `ALLOW` / `BLOCK` / `REQUIRE_APPROVAL`
