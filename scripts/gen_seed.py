#!/usr/bin/env python3
"""Idempotent seed: rebuild seed/orders.csv and seed/warehouse.duckdb."""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running without install during early bootstrap.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from write_gate.db import ORDERS_DDL, connect  # noqa: E402
from write_gate.paths import DB_PATH, ORDERS_CSV, SEED_DIR  # noqa: E402

AS_OF = date(2026, 9, 2)
N_ROWS = 120
STATUSES = ("paid", "pending", "cancelled", "refunded")


def _dt_for(i: int) -> date:
    """Even rows → expired (before 2026-08-26); odd rows → fresh."""
    if i % 2 == 0:
        # 2026-07-01 .. 2026-08-25
        return date(2026, 7, 1) + timedelta(days=(i // 2) % 56)
    # 2026-08-26 .. 2026-09-02
    return date(2026, 8, 26) + timedelta(days=(i // 2) % 8)


def build_rows() -> list[dict[str, object]]:
    rows = []
    for i in range(1, N_ROWS + 1):
        dt = _dt_for(i)
        rows.append(
            {
                "order_id": i,
                "user_id": 1000 + (i % 40),
                "amount": round(10.0 + (i * 1.37) % 90.0, 2),
                "dt": dt.isoformat(),
                "email": f"user{i}@example.com",
                "phone": f"138{i:08d}",
                "status": STATUSES[i % len(STATUSES)],
            }
        )
    return rows


def write_csv(rows: list[dict[str, object]]) -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["order_id", "user_id", "amount", "dt", "email", "phone", "status"]
    with ORDERS_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_duckdb(rows: list[dict[str, object]]) -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for leftover in (DB_PATH, Path(str(DB_PATH) + ".wal")):
        if leftover.exists():
            leftover.unlink()
    conn = connect(DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS orders")
        conn.execute(ORDERS_DDL)
        conn.executemany(
            "INSERT INTO orders (order_id, user_id, amount, dt, email, phone, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["order_id"],
                    r["user_id"],
                    r["amount"],
                    r["dt"],
                    r["email"],
                    r["phone"],
                    r["status"],
                )
                for r in rows
            ],
        )
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE dt < DATE '2026-08-26'"
        ).fetchone()[0]
        fresh = n - expired
        print(f"seeded {DB_PATH}")
        print(f"  rows={n} expired_partitions={expired} fresh_partitions={fresh}")
        print(f"  csv={ORDERS_CSV}")
        print(f"  as_of={AS_OF.isoformat()} cutoff=2026-08-26")
    finally:
        conn.close()


def main() -> int:
    rows = build_rows()
    write_csv(rows)
    write_duckdb(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
