#!/usr/bin/env python3
"""Thin PreToolUse entry. Prefer: sql-write-gate hook"""

from __future__ import annotations

import sys

from write_gate.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["hook", *sys.argv[1:]]))
