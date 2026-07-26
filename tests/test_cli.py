from __future__ import annotations

from pathlib import Path

from aflow_cli.cli import main


def test_demo_review_and_bundle_validation(tmp_path, capsys):
    output = tmp_path / "demo"
    assert main(["demo", "--output-dir", str(output)]) == 0
    capsys.readouterr()
    assert main(["validate-bundle", "--output-dir", str(output)]) == 0
    assert '"valid": true' in capsys.readouterr().out


def test_review_preserves_original_file(tmp_path):
    source = tmp_path / "plan.md"
    source.write_text("Objective: test\nValidation: test\nFixture: test\nSuccess criteria: test\n", encoding="utf-8")
    before = source.read_bytes()
    assert main(["review", "--plan", str(source), "--plan-id", "p", "--output-dir", str(tmp_path / "bundle")]) == 0
    assert source.read_bytes() == before
