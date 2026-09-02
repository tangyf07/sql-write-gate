"""Destructive SQL guard: delete/update without WHERE, DROP, TRUNCATE, ALTER."""

from write_gate.catalog import load_catalog
from write_gate.config import demo_policy, production_policy
from write_gate.engine import evaluate


def _eval(sql: str, *, production: bool = False):
    policy = production_policy() if production else demo_policy()
    return evaluate(sql, load_catalog(), policy=policy)


def test_delete_without_where_blocked():
    ev = _eval("DELETE FROM users")
    assert ev.action == "BLOCK"
    assert ev.allowed is False
    assert ev.rule_id == "delete_without_where"
    assert ev.risk == "critical"
    assert ev.operation == "delete"


def test_delete_from_orders_without_where_blocked():
    ev = _eval("DELETE FROM orders;", production=True)
    assert ev.action == "BLOCK"
    assert ev.rule_id == "delete_without_where"
    assert ev.risk == "critical"


def test_update_without_where_blocked():
    ev = _eval("UPDATE orders SET status = 'expired'")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "update_without_where"
    assert ev.risk == "critical"


def test_drop_table_blocked():
    ev = _eval("DROP TABLE orders")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "drop_table"


def test_truncate_table_blocked():
    ev = _eval("TRUNCATE TABLE orders")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "truncate_table"


def test_alter_table_blocked():
    ev = _eval("ALTER TABLE orders ADD COLUMN note VARCHAR")
    assert ev.action == "BLOCK"
    assert ev.rule_id == "alter_table"


def test_delete_with_where_not_destructive():
    ev = _eval("DELETE FROM orders WHERE order_id = 1")
    assert ev.rule_id != "delete_without_where"
