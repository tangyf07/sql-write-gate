"""sql-write-gate init scaffolds starter files without clobbering unless --force."""

from __future__ import annotations

from pathlib import Path

from write_gate.cli import main
from write_gate.init import STARTER_FILES, init_project


def test_init_into_empty_dir(tmp_path: Path):
    result = init_project(tmp_path)
    assert sorted(result.created) == sorted(rel for _, rel in STARTER_FILES)
    assert result.skipped == []
    assert result.overwritten == []
    assert (tmp_path / "policy.yaml").is_file()
    assert (tmp_path / "catalog.json").is_file()
    assert (tmp_path / "GETTING_STARTED.md").is_file()
    policy = (tmp_path / "policy.yaml").read_text(encoding="utf-8")
    assert "environment: production" in policy
    assert "delete: block" in policy


def test_init_second_without_force_skips(tmp_path: Path):
    init_project(tmp_path)
    marker = "# custom-marker-do-not-overwrite\n"
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(marker + policy_path.read_text(encoding="utf-8"), encoding="utf-8")
    before = {rel: (tmp_path / rel).read_text(encoding="utf-8") for _, rel in STARTER_FILES}

    result = init_project(tmp_path, force=False)
    assert result.created == []
    assert result.overwritten == []
    assert sorted(result.skipped) == sorted(rel for _, rel in STARTER_FILES)
    for rel, content in before.items():
        assert (tmp_path / rel).read_text(encoding="utf-8") == content
    assert marker in (tmp_path / "policy.yaml").read_text(encoding="utf-8")


def test_init_with_force_overwrites(tmp_path: Path):
    init_project(tmp_path)
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("environment: hacked\nrules: {}\n", encoding="utf-8")

    result = init_project(tmp_path, force=True)
    assert result.created == []
    assert sorted(result.overwritten) == sorted(rel for _, rel in STARTER_FILES)
    assert result.skipped == []
    text = policy_path.read_text(encoding="utf-8")
    assert "environment: production" in text
    assert "hacked" not in text


def test_init_cli_exit_zero_and_lists_files(tmp_path: Path, capsys):
    code = main(["init", "--dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "created: policy.yaml" in out
    assert "created: catalog.json" in out
    assert "created: GETTING_STARTED.md" in out

    code2 = main(["init", "--dir", str(tmp_path)])
    assert code2 == 0
    out2 = capsys.readouterr().out
    assert "skipped (exists): policy.yaml" in out2
    assert "--force" in out2


def test_init_registered_in_help(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_out = capsys.readouterr().out
    assert "init" in help_out
