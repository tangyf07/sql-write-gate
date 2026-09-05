"""Regression: pip-installed wheel + init + bare check must BLOCK, not FileNotFoundError."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_install_init_bare_check(tmp_path):
    """Build current tree into a wheel, install into a temp venv, init + bare check."""
    outdir = tmp_path / "dist"
    outdir.mkdir()
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "build"],
        cwd=str(ROOT),
    )
    subprocess.check_call(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)],
        cwd=str(ROOT),
    )
    wheels = sorted(outdir.glob("sql_write_gate-*.whl"))
    assert wheels, "expected a wheel under temp dist/"
    wheel = wheels[-1]

    venv = tmp_path / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    swg = venv / "bin" / "sql-write-gate"
    subprocess.check_call([str(pip), "install", "-q", str(wheel)])

    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.check_call([str(swg), "init", "--dir", str(proj)], cwd=str(proj))
    proc = subprocess.run(
        [str(swg), "check", "DELETE FROM orders"],
        cwd=str(proj),
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "delete_without_where" in out
    assert "FileNotFoundError" not in out
