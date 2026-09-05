"""Default policy/catalog prefer cwd init files; installed layout has no fake seed/."""

from __future__ import annotations

from pathlib import Path

import write_gate.paths as paths
from write_gate.paths import (
    CHECKOUT_ROOT,
    default_approvals_path,
    default_audit_path,
    default_catalog_path,
    default_db_path,
    default_log_dir,
    default_policy_path,
)


def test_checkout_root_detected_in_dev_tree():
    assert CHECKOUT_ROOT is not None
    assert (CHECKOUT_ROOT / "seed" / "catalog.json").is_file()
    assert (CHECKOUT_ROOT / "pyproject.toml").is_file()


def test_find_checkout_root_rejects_site_packages_layout(tmp_path, monkeypatch):
    fake_pkg = tmp_path / "lib" / "python3.13" / "site-packages" / "write_gate"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE_DIR", fake_pkg)
    assert paths._find_checkout_root() is None


def test_cwd_init_files_win_over_seed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "policy.yaml").write_text("environment: production\nrules: {}\nlimits: {}\n")
    (tmp_path / "catalog.json").write_text(
        '{"as_of_date":"2026-09-02","freshness_days":7,"tables":{}}'
    )
    assert default_policy_path() == tmp_path / "policy.yaml"
    assert default_catalog_path() == tmp_path / "catalog.json"


def test_installed_layout_defaults_to_cwd(tmp_path, monkeypatch):
    """Simulate wheel install: package dir without repo seed/ above it."""
    fake_pkg = tmp_path / "site-packages" / "write_gate"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE_DIR", fake_pkg)
    monkeypatch.setattr(paths, "CHECKOUT_ROOT", None)
    work = tmp_path / "proj"
    work.mkdir()
    monkeypatch.chdir(work)
    (work / "policy.yaml").write_text("environment: production\nrules: {}\nlimits: {}\n")
    (work / "catalog.json").write_text(
        '{"as_of_date":"2026-09-02","freshness_days":7,"tables":{"orders":{"columns":{},'
        '"allowed_write_columns":[],"pii_columns":[],"restricted_columns":[]}}}'
    )
    assert default_policy_path() == work / "policy.yaml"
    assert default_catalog_path() == work / "catalog.json"
    assert default_db_path() == work / "warehouse.duckdb"
    assert default_log_dir() == work / ".logs"
    assert default_audit_path() == work / ".logs" / "audit.jsonl"
    assert default_approvals_path() == work / ".logs" / "approvals.jsonl"
    # Must not point into site-packages parents
    assert "site-packages" not in str(default_catalog_path())
    assert "site-packages" not in str(default_policy_path())
