"""Project paths. Warehouse and catalog live under seed/."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
SEED_DIR = PROJECT_ROOT / "seed"
DB_PATH = SEED_DIR / "warehouse.duckdb"
CATALOG_PATH = SEED_DIR / "catalog.json"
ORDERS_CSV = SEED_DIR / "orders.csv"
