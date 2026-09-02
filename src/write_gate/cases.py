"""Canonical demo / test SQL. Kept in one place so Makefile demo and pytest stay aligned."""

LEGAL_WRITE_SQL = (
    "INSERT INTO orders (order_id, user_id, amount, dt, status) "
    "VALUES (900001, 42, 18.50, '2026-09-01', 'paid')"
)

EXPIRED_WRITE_SQL = (
    "INSERT INTO orders (order_id, user_id, amount, dt, status) "
    "VALUES (900002, 42, 18.50, '2026-08-01', 'paid')"
)

PII_WRITE_SQL = (
    "INSERT INTO orders (order_id, user_id, amount, dt, status, email) "
    "VALUES (900003, 42, 18.50, '2026-09-01', 'paid', 'eve@example.com')"
)

SCHEMA_MISMATCH_SQL = (
    "INSERT INTO orders (order_id, user_id, amount, dt, status, not_a_column) "
    "VALUES (900004, 42, 18.50, '2026-09-01', 'paid', 1)"
)

TYPE_MISMATCH_SQL = (
    "INSERT INTO orders (order_id, user_id, amount, dt, status) "
    "VALUES ('not-an-int', 42, 18.50, '2026-09-01', 'paid')"
)

READ_ONLY_SQL = (
    "SELECT user_id, amount, dt, status FROM orders WHERE dt >= '2026-08-26' LIMIT 5"
)
