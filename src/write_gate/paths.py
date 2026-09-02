"""Project paths. Warehouse and catalog live under seed/."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
SEED_DIR = PROJECT_ROOT / "seed"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
LOG_DIR = PROJECT_ROOT / ".logs"

DB_PATH = SEED_DIR / "warehouse.duckdb"
CATALOG_PATH = SEED_DIR / "catalog.json"
ORDERS_CSV = SEED_DIR / "orders.csv"

POLICY_PATH = PROJECT_ROOT / "policy.yaml"
EXAMPLES_POLICY_PATH = EXAMPLES_DIR / "policy.yaml"
DEMO_POLICY_PATH = EXAMPLES_DIR / "policy.demo.yaml"
EXAMPLES_CATALOG_PATH = EXAMPLES_DIR / "catalog.json"
AUDIT_PATH = LOG_DIR / "audit.jsonl"
APPROVALS_PATH = LOG_DIR / "approvals.jsonl"
