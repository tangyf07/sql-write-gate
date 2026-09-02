"""CLI: python -m write_gate check|exec <sql>"""

from __future__ import annotations

import argparse
import json
import sys

from write_gate.wrapper import WriteGate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m write_gate",
        description="写库前门禁: 对 SQL 做确定性预写检查（无 API Key / 无 LLM）",
    )
    parser.add_argument("action", choices=["check", "exec"], help="只检查或检查后执行")
    parser.add_argument("sql", help="一条 SQL")
    args = parser.parse_args(argv)

    with WriteGate() as gate:
        if args.action == "check":
            evidence = gate.check(args.sql)
            result = None
        else:
            evidence, result = gate.execute(args.sql)

    payload = evidence.to_dict()
    if result is not None and evidence.allowed:
        try:
            rows = result.fetchall()
            payload["rows"] = [list(r) for r in rows]
            payload["rowcount"] = len(payload["rows"])
        except Exception:
            payload["rowcount"] = getattr(result, "rowcount", None)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if evidence.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
