"""Optional live Postgres tests. Skipped unless DATABASE_URL is a postgres URL."""

from __future__ import annotations

import os

import pytest

from write_gate.config import production_policy
from write_gate.wrapper import WriteGate

_URL = os.environ.get("DATABASE_URL") or ""
_LIVE = _URL.lower().startswith(("postgres://", "postgresql://"))

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="DATABASE_URL not set to a postgres:// or postgresql:// URL",
)


def test_live_check_delete_without_where():
    gate = WriteGate(database=_URL, policy=production_policy())
    ev = gate.check("DELETE FROM orders")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "delete_without_where"


def test_live_execute_delete_without_where_no_orders_table_required():
    gate = WriteGate(database=_URL, policy=production_policy())
    ev, result = gate.execute("DELETE FROM orders")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "delete_without_where"
    assert result is None
