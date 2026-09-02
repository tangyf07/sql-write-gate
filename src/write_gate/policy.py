"""Backward-compatible evaluate() entry point. Engine + guards do the real work."""

from __future__ import annotations

from typing import Any

from write_gate.catalog import Catalog
from write_gate.config import Policy, demo_policy
from write_gate.decision import (
    RULE_EXPIRED,
    RULE_OK,
    RULE_PII,
    RULE_SCHEMA,
    Decision,
    Evidence,
)
from write_gate.engine import evaluate as engine_evaluate

__all__ = [
    "Evidence",
    "Decision",
    "evaluate",
    "RULE_OK",
    "RULE_PII",
    "RULE_EXPIRED",
    "RULE_SCHEMA",
]


def evaluate(
    sql: str,
    catalog: Catalog,
    policy: Policy | None = None,
    conn: Any | None = None,
) -> Decision:
    """Return a gate verdict for a single SQL string.

    Library callers (tests / demo helpers) default to the demo policy so
    existing legal-INSERT cases stay ALLOW. The CLI loads production
    policy.yaml instead.
    """
    return engine_evaluate(sql, catalog, policy=policy or demo_policy(), conn=conn)
