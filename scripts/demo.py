#!/usr/bin/env python3
"""Print the three canonical gate cases: legal / expired / PII. No API key."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from write_gate.cases import EXPIRED_WRITE_SQL, LEGAL_WRITE_SQL, PII_WRITE_SQL  # noqa: E402
from write_gate.paths import DB_PATH, DEMO_POLICY_PATH  # noqa: E402
from write_gate.wrapper import WriteGate  # noqa: E402


CASES = [
    ("用例 1 · 合法写入（新鲜分区 + 非 PII 列）", LEGAL_WRITE_SQL, True, "ok"),
    ("用例 2 · 过期分区写入", EXPIRED_WRITE_SQL, False, "expired_partition"),
    ("用例 3 · PII 列写入", PII_WRITE_SQL, False, "pii_column"),
]


def _banner(title: str) -> None:
    line = "=" * 72
    print(line)
    print(title)
    print(line)


def main() -> int:
    rc = 0
    with WriteGate(db_path=DB_PATH, policy_path=DEMO_POLICY_PATH) as gate:
        for title, sql, expect_allow, expect_rule in CASES:
            _banner(title)
            print(f"SQL:\n  {sql}")
            evidence, result = gate.execute(sql)
            verdict = "ALLOWED" if evidence.allowed else "BLOCKED"
            print(f"VERDICT: {verdict}")
            print(f"rule_id: {evidence.rule_id}")
            print(f"message: {evidence.message}")
            print("evidence:")
            print(json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2))
            if evidence.allowed and result is not None:
                n = gate.conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id = 900001"
                ).fetchone()[0]
                print(f"executed: warehouse row order_id=900001 present={int(n)}")
            else:
                print("executed: no (gate blocked write; DuckDB write API not called)")
            if evidence.allowed != expect_allow or evidence.rule_id != expect_rule:
                print(
                    f"UNEXPECTED: expected allowed={expect_allow} rule_id={expect_rule}"
                )
                rc = 1
            print()
    if rc == 0:
        print("demo: three cases matched expected verdicts")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
