# sql-write-gate — getting started

```bash
pip install sql-write-gate   # or from clone: pip install -e .
sql-write-gate init       # already done if you see this file
sql-write-gate check "DELETE FROM orders"
# → BLOCKED  rule=delete_without_where
```

Optional warehouse:

```bash
# DuckDB file from a clone’s seed/ (or your own .duckdb):
sql-write-gate check --db path/to/warehouse.duckdb --catalog catalog.json "DELETE FROM orders"

# or URL (Postgres / MySQL / SQLite):
sql-write-gate check --database "$DATABASE_URL" "DELETE FROM orders"
```

Edit `policy.yaml` and `catalog.json` here. Pass `--policy` / `--catalog` if needed.
