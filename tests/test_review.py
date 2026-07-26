from __future__ import annotations

from pathlib import Path

import pytest

from aflow_cli.core import ReviewError, review_plan, validate_bundle, write_bundle


FIXTURES = Path(__file__).parent / "fixtures"


def test_complete_plan_creates_evaluation_without_changing_source(tmp_path):
    source = (FIXTURES / "complete-plan.md").read_text(encoding="utf-8")
    before = source
    bundle = review_plan(original_plan=source, plan_id="complete")
    assert bundle["manifest"]["outcome"] == "review_processed_all_gaps_disposed"
    summary = bundle["manifest"]["processing_summary"]
    assert summary["plan_processed"] is True
    assert summary["evaluation_completed"] is True
    assert summary["gap_count_limit_applied"] is False
    assert summary["gaps_discovered"] == 0
    assert source == before
    assert "original_plan_digest" not in bundle["manifest"]
    assert validate_bundle(bundle)["valid"] is True
    assert write_bundle(bundle, tmp_path)["state"] == "written"
    assert write_bundle(bundle, tmp_path)["state"] == "idempotent"
    assert (tmp_path / "evaluation.json").exists()
    assert (tmp_path / "validation-fixtures.json").exists()
    assert (tmp_path / "validation-tests.json").exists()


def test_design_decision_never_rewrites_plan():
    source = (FIXTURES / "design-decision-plan.md").read_text(encoding="utf-8")
    bundle = review_plan(original_plan=source, plan_id="decision")
    assert bundle["manifest"]["outcome"] == "decision_required"
    assert "decision-required.json" in bundle["artifacts"]
    assert "original_plan_digest" not in bundle["manifest"]


def test_malformed_bundle_is_rejected_without_hash_contract():
    bundle = review_plan(original_plan="Objective: x", plan_id="x")
    bundle["manifest"]["artifacts"].remove("gap-review.json")
    with pytest.raises(ReviewError, match="manifest"):
        validate_bundle(bundle)
