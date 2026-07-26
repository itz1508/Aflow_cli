"""Stage 10: Fixture development after success criteria - traceable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class FixtureCase:
    fixture_id: str
    fixture_type: Literal["normal", "edge", "failure", "regression"]
    covered_criterion_ids: list[str]
    covered_validation_ids: list[str]
    setup: list[str]
    input: dict[str, Any]
    expected_result: str
    required_evidence: list[str]


def develop_fixtures(
    *,
    success_criteria: list[Any],
    validations: list[Any],
) -> list[FixtureCase]:
    fixtures: list[FixtureCase] = []
    validation_by_id = {v.validation_id: v for v in validations}

    for criterion in success_criteria:
        cid = criterion.criterion_id
        vid = f"validation-{cid}"
        v = validation_by_id.get(vid)
        if not v:
            continue

        # Decide fixture matrix based on criterion type
        if cid in ("validation-pass", "fixture-pass", "cli-equivalence", "mcp-unknown-rejection", "success-criteria-assessment", "final-result-persistence"):
            # normal + regression
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-normal",
                fixture_type="normal",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=[f"Ensure operation_dir exists, validation {vid} defined"],
                input={"validation_id": vid, "criterion_id": cid, "command": v.command_or_assertion, "expected": v.expected_result},
                expected_result=v.expected_result,
                required_evidence=v.required_evidence,
            ))
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-regression",
                fixture_type="regression",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=["Restore previous valid revision if exists"],
                input={"validation_id": vid, "mode": "regression", "previous_state": "completed"},
                expected_result=v.expected_result + " still holds after regression restore",
                required_evidence=v.required_evidence + ["operation-config.json"],
            ))
        elif cid in ("validation-fail", "fixture-fail"):
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-failure",
                fixture_type="failure",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=[f"Define failing command for {cid}", "Create evidence dir"],
                input={"validation_id": vid, "command": "exit 1", "expected_to_fail": True},
                expected_result="failed with evidence preserved, previous revision intact",
                required_evidence=v.required_evidence,
            ))
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-normal-override",
                fixture_type="normal",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=["After failure record failure then pass can supersede but both retained"],
                input={"validation_id": vid, "supersede": True, "later_result": "passed"},
                expected_result="later passing can supersede failure but both records remain per validation-report contract",
                required_evidence=v.required_evidence + ["validation-report.json: both failed and passed preserved"],
            ))
        elif cid in ("validation-timeout", ):
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-edge-timeout",
                fixture_type="edge",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=["Define command that exceeds 120s bound", "Mock timer or short sleep with timeout param 1s for test"],
                input={"validation_id": vid, "command": "sleep 130", "timeout_sec": 1, "expected_status": "timed_out"},
                expected_result="timed_out with duration recorded",
                required_evidence=v.required_evidence,
            ))
        elif cid in ("validation-unsupported", "malformed-fixture-rejection", "repo-boundary-enforcement"):
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-failure-unsupported",
                fixture_type="failure",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=[f"Define unsupported/malformed input for {cid}"],
                input={"validation_id": vid, "malformed": True, "command": "unsupported:syntax::"},
                expected_result="unsupported or not_run with schema violation evidence, no shell execution",
                required_evidence=v.required_evidence,
            ))
        elif cid in ("failed-evidence-reopens-only-affected", "stale-evidence-reopens-only-affected", "unrelated-unchanged", "revision-conflict-rejection", "concurrent-protection", "interrupted-write-preservation", "resume-after-verification", "no-review-rerun-during-verify", "no-conversation-stored"):
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-normal",
                fixture_type="normal",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=[f"Setup operation with two distinct validations covering distinct steps for {cid}"],
                input={"validation_id": vid, "scenario": cid, "steps": ["step-001", "step-002"], "distinct_coverage": True},
                expected_result=v.expected_result,
                required_evidence=v.required_evidence,
            ))
        else:
            # generic
            fixtures.append(FixtureCase(
                fixture_id=f"fixture-{cid}-normal",
                fixture_type="normal",
                covered_criterion_ids=[cid],
                covered_validation_ids=[vid],
                setup=[],
                input={"criterion_id": cid, "validation_id": vid},
                expected_result=v.expected_result,
                required_evidence=v.required_evidence,
            ))

    # Ensure at least one fixture
    if not fixtures and validations:
        v = validations[0]
        fixtures.append(FixtureCase(
            fixture_id=f"fixture-{v.validation_id}-normal",
            fixture_type="normal",
            covered_criterion_ids=v.covered_criterion_ids,
            covered_validation_ids=[v.validation_id],
            setup=[],
            input={},
            expected_result=v.expected_result,
            required_evidence=v.required_evidence,
        ))

    return fixtures
