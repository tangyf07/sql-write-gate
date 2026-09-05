"""Scaffold a starter sql-write-gate project (policy, catalog, getting started)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# (template filename, destination relative path)
STARTER_FILES: tuple[tuple[str, str], ...] = (
    ("policy.yaml", "policy.yaml"),
    ("catalog.json", "catalog.json"),
    ("GETTING_STARTED.md", "GETTING_STARTED.md"),
)


@dataclass(frozen=True)
class InitResult:
    created: list[str]
    skipped: list[str]
    overwritten: list[str]

    @property
    def all_paths(self) -> list[str]:
        return [*self.created, *self.skipped, *self.overwritten]


def templates_dir() -> Path:
    """Resolve packaged templates (editable install or wheel)."""
    path = TEMPLATES_DIR
    if not path.is_dir():
        raise FileNotFoundError(f"sql-write-gate templates missing: {path}")
    return path


def init_project(target_dir: Path | str = ".", *, force: bool = False) -> InitResult:
    """Create starter files under *target_dir*.

    Without *force*, existing files are left unchanged and listed as skipped.
    With *force*, existing files are overwritten.
    """
    dest_root = Path(target_dir).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    src_root = templates_dir()

    created: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []

    for template_name, rel in STARTER_FILES:
        src = src_root / template_name
        if not src.is_file():
            raise FileNotFoundError(f"template not found: {src}")
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        label = str(dest.relative_to(dest_root)) if dest.is_relative_to(dest_root) else str(dest)

        if dest.exists() and not force:
            skipped.append(label)
            continue
        if dest.exists() and force:
            shutil.copyfile(src, dest)
            overwritten.append(label)
        else:
            shutil.copyfile(src, dest)
            created.append(label)

    return InitResult(created=created, skipped=skipped, overwritten=overwritten)


def format_init_report(result: InitResult, target_dir: Path | str) -> str:
    root = Path(target_dir).expanduser().resolve()
    lines = [f"sql-write-gate init → {root}"]
    for path in result.created:
        lines.append(f"  created: {path}")
    for path in result.overwritten:
        lines.append(f"  overwritten: {path}")
    for path in result.skipped:
        lines.append(f"  skipped (exists): {path}")
    if not result.created and not result.overwritten and result.skipped:
        lines.append("  (nothing written; use --force to overwrite)")
    return "\n".join(lines) + "\n"
