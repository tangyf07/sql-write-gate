"""Policy-only tests (no DuckDB execute, no network, no LLM)."""

from write_gate.cases import (
    EXPIRED_WRITE_SQL,
    LEGAL_WRITE_SQL,
    PII_WRITE_SQL,
    READ_ONLY_SQL,
    SCHEMA_MISMATCH_SQL,
    TYPE_MISMATCH_SQL,
)
from write_gate.catalog import load_catalog
from write_gate.policy import evaluate


def _catalog():
    return load_catalog()


def test_read_only_select_allowed():
    ev = evaluate(READ_ONLY_SQL, _catalog())
    assert ev.allowed is True
    assert ev.rule_id == "ok"


def test_legal_insert_allowed():
    ev = evaluate(LEGAL_WRITE_SQL, _catalog())
    assert ev.allowed is True
    assert ev.rule_id == "ok"


def test_expired_partition_rejected():
    ev = evaluate(EXPIRED_WRITE_SQL, _catalog())
    assert ev.allowed is False
    assert ev.rule_id == "expired_partition"
    assert "2026-08-01" in ev.message
    assert "2026-08-26" in ev.message


def test_pii_insert_rejected():
    ev = evaluate(PII_WRITE_SQL, _catalog())
    assert ev.allowed is False
    assert ev.rule_id == "pii_column"
    assert "email" in ev.message


def test_schema_unknown_column_rejected():
    ev = evaluate(SCHEMA_MISMATCH_SQL, _catalog())
    assert ev.allowed is False
    assert ev.rule_id == "schema_mismatch"
    assert "not_a_column" in ev.message


def test_schema_type_mismatch_rejected():
    ev = evaluate(TYPE_MISMATCH_SQL, _catalog())
    assert ev.allowed is False
    assert ev.rule_id == "schema_mismatch"


def test_unknown_table_rejected():
    ev = evaluate(
        "INSERT INTO no_such_table (user_id) VALUES (1)",
        _catalog(),
    )
    assert ev.allowed is False
    assert ev.rule_id == "schema_mismatch"


def test_pii_update_rejected():
    ev = evaluate(
        "UPDATE orders SET phone = '13900000000' WHERE dt = '2026-09-01'",
        _catalog(),
    )
    assert ev.allowed is False
    assert ev.rule_id == "pii_column"


def test_update_expired_partition_rejected():
    ev = evaluate(
        "UPDATE orders SET amount = 1.0 WHERE dt = '2026-08-01'",
        _catalog(),
    )
    assert ev.allowed is False
    assert ev.rule_id == "expired_partition"


def test_select_star_allowed():
    ev = evaluate("SELECT * FROM orders", _catalog())
    assert ev.allowed is True
    assert ev.rule_id == "ok"
