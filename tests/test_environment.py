"""Environment / approval policy tests."""

from write_gate.cases import LEGAL_WRITE_SQL, READ_ONLY_SQL
from write_gate.catalog import load_catalog
from write_gate.config import production_policy
from write_gate.engine import evaluate


def test_production_insert_requires_approval():
    ev = evaluate(LEGAL_WRITE_SQL, load_catalog(), policy=production_policy())
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.allowed is False
    assert ev.rule_id == "environment_policy"
    assert ev.operation == "insert"


def test_production_select_without_pii_allowed():
    ev = evaluate(READ_ONLY_SQL, load_catalog(), policy=production_policy())
    assert ev.action == "ALLOW"
    assert ev.rule_id == "ok"


def test_production_update_requires_approval():
    ev = evaluate(
        "UPDATE orders SET status='expired' WHERE order_id=123",
        load_catalog(),
        policy=production_policy(),
    )
    assert ev.action == "REQUIRE_APPROVAL"
    assert ev.rule_id == "environment_policy"
    assert ev.operation == "update"


def test_production_ddl_blocked_via_destructive_or_env():
    ev = evaluate("CREATE TABLE t (id INTEGER)", load_catalog(), policy=production_policy())
    assert ev.action == "BLOCK"
    # CREATE is not in the destructive family; environment ddl=block.
    assert ev.rule_id in {"environment_policy", "drop_table"}
