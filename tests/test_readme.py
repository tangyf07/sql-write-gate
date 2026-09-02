"""README first screen lists the six CLI commands."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("check", "hook", "mcp", "proxy", "approve", "audit")


def test_readme_lists_six_commands_above_the_fold():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## v0.2" in text
    fold = text.split("## v0.2", 1)[0]
    for name in COMMANDS:
        assert name in fold, f"{name!r} missing from README first screen (before v0.2)"
