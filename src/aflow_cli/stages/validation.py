"""Stage 9: Validation development from success criteria - traceable to criteria."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationSpec:
    validation_id: str
    covered_criterion_ids: list[str]
    command_or_assertion: str
    expected_result: str
    required_evidence: list[str]


def develop_validations(
    *,
    success_criteria: list[Any],
    repository_evidence: Any = None,
) -> list[ValidationSpec]:
    validations: list[ValidationSpec] = []

    for criterion in success_criteria:
        cid = criterion.criterion_id

        # Map each criterion to concrete validation
        if cid == "cli-equivalence":
            cmd = "Inspect src/aflow_cli/cli.py main and src/aflow_cli/mcp_server.py: both call shared service src/aflow_cli/work_state.py and verification service (once implemented)"
            exp = "CLI and MCP equivalence proven via code path inspection"
            ev = ["src/aflow_cli/cli.py", "src/aflow_cli/mcp_server.py", "src/aflow_cli/work_state.py"]
        elif cid == "mcp-unknown-rejection":
            cmd = "uv run pytest -q tests/test_mcp_contract.py tests/test_trigger_schema.py - verifies additionalProperties false"
            exp = "MCP unknown properties rejected"
            ev = ["tests/test_mcp_contract.py", "src/aflow_cli/mcp_server.py"]
        elif cid == "validation-pass":
            cmd = "uv run pytest -q --basetemp external -o cache_dir=external to validate passing command handling"
            exp = "passed"
            ev = ["validation-report.json", "evidence/validation-pass.txt"]
        elif cid == "validation-fail":
            cmd = "pytest command that exits 1 producing failed"
            exp = "failed with preserved evidence"
            ev = ["evidence/validation-fail.txt", "validation-report.json"]
        elif cid == "validation-timeout":
            cmd = "command that sleeps >120s"
            exp = "timed_out with partial evidence"
            ev = ["evidence/validation-timeout.txt"]
        elif cid == "validation-unsupported":
            cmd = "unsupported command detection via allowlist check"
            exp = "unsupported without shell execution"
            ev = ["src/aflow_cli/work_state.py"]
        elif cid == "fixture-pass":
            cmd = "fixture normal execution: setup -> exec -> compare -> cleanup"
            exp = "passed"
            ev = ["fixture-cases.json", "evidence/fixture-pass.txt"]
        elif cid == "fixture-fail":
            cmd = "fixture with mismatch"
            exp = "failed with captured expected vs actual"
            ev = ["evidence/fixture-fail.txt"]
        elif cid == "malformed-fixture-rejection":
            cmd = "assert malformed fixture missing input raises not_run"
            exp = "not_run with schema violation evidence"
            ev = ["fixture-cases.json"]
        elif cid == "success-criteria-assessment":
            cmd = "inspect final-result.json: every criterion has assessment with required fields"
            exp = "every persisted success criterion has assessment satisfied/not_satisfied/not_verified/stale"
            ev = ["final-result.json", "success-criteria.json"]
        elif cid == "failed-evidence-reopens-only-affected":
            cmd = "uv run pytest -q tests/test_continuity.py::test_F_failed_reopens -k covers failed reopen"
            exp = "only covered step reopened"
            ev = ["work-status.json", "validation-report.json"]
        elif cid == "stale-evidence-reopens-only-affected":
            cmd = "uv run pytest -q tests/test_continuity.py::test_G_stale_reopens_only_covered"
            exp = "stale reopens only covered"
            ev = ["work-status.json", "validation-report.json"]
        elif cid == "unrelated-unchanged":
            cmd = "inspect unchanged completed steps count after single failure"
            exp = "unrelated steps unchanged"
            ev = ["updated-plan.json"]
        elif cid == "final-result-persistence":
            cmd = "inspect operation_dir/final-result.json atomic write"
            exp = "final result persisted atomically with completion_time ISO-8601 UTC"
            ev = ["final-result.json", "operation-config.json"]
        elif cid == "revision-conflict-rejection":
            cmd = "uv run pytest -q tests/test_continuity.py::test_N_stale_revision_rejected"
            exp = "stale expected_revision rejected, no overwrite"
            ev = ["work-status.json"]
        elif cid == "concurrent-protection":
            cmd = "uv run pytest -q tests/test_continuity.py::test_O_concurrent_protection"
            exp = "concurrent update protection via O_EXCL lock"
            ev = ["work_state.py .lock handling"]
        elif cid == "interrupted-write-preservation":
            cmd = "uv run pytest -q tests/test_continuity.py::test_P_atomic_preservation"
            exp = "interrupted write preserves previous revision"
            ev = ["updated-plan.json .tmp handling"]
        elif cid == "resume-after-verification":
            cmd = "uv run pytest -q tests/test_continuity.py::test_R_resume_operation_id_only"
            exp = "resume by operation_id returns latest revision"
            ev = ["continuation-package.json"]
        elif cid == "no-conversation-stored":
            cmd = "uv run pytest -q tests/test_continuity.py::test_T_no_conversation"
            exp = "no conversation phrases persisted"
            ev = ["updated-plan.json", "work-status.json"]
        elif cid == "repo-boundary-enforcement":
            cmd = "Attempt validation outside repository root, expect rejection"
            exp = "path traversal rejected"
            ev = ["src/aflow_cli/work_state.py path checks"]
        elif cid == "no-review-rerun-during-verify":
            cmd = "uv run pytest -q tests/test_continuity.py::test_K_no_canonical_rerun"
            exp = "no canonical rerun during verification/resume"
            ev = ["operation-config.json stage_state"]
        elif cid.startswith("corrected-"):
            cmd = "inspect correction artifact"
            exp = "correction verified"
            ev = ["gap-resolution-history.json"]
        else:
            cmd = f"inspect {cid} implementation"
            exp = f"{cid} observable outcome matches plan"
            ev = [f"{cid} evidence"]

        validations.append(ValidationSpec(
            validation_id=f"validation-{cid}",
            covered_criterion_ids=[cid],
            command_or_assertion=cmd,
            expected_result=exp,
            required_evidence=ev,
        ))

    if not validations:
        validations.append(ValidationSpec(
            validation_id="validation-plan-immutability",
            covered_criterion_ids=["plan-immutable"],
            command_or_assertion="compare original-plan.md with input",
            expected_result="identical",
            required_evidence=["original-plan.md"],
        ))

    return validations
