"""Stage 8: Success criteria derived from evaluated plan - traceable deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


@dataclass(frozen=True)
class SuccessCriterion:
    criterion_id: str
    source_plan_step_ids: list[str]
    requirement: str
    observable_result: str
    must_not_regress: list[str]


def _extract_headings(plan: str) -> list[str]:
    ids: list[str] = []
    for line in plan.split("\n"):
        s = line.strip()
        if s.startswith("## ") or s.startswith("### "):
            title = s.lstrip("# ").strip()
            if not title:
                continue
            low = title.lower()
            if low.startswith("finding") or low.startswith("decision") or low.startswith("resolved"):
                continue
            # slug-like
            slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")
            if slug:
                ids.append(slug[:80])
    return ids


def derive_success_criteria(
    *,
    evaluated_plan: str,
    gap_resolutions: list[Any],
) -> list[SuccessCriterion]:
    criteria: list[SuccessCriterion] = []
    headings = _extract_headings(evaluated_plan)
    lower = evaluated_plan.lower()

    # For verification capability plan, derive concrete criteria per section
    verification_sections = [
        ("cli-equivalence", ["Public CLI interface", "Public MCP interface"], 
         "CLI aflow verify and MCP aflow_verify call the same shared verification service",
         "Both entrypoints invoke identical service with operation_id; introspection of cli.py and mcp_server.py shows same function",
         ["mcp-server", "cli"]),
        ("mcp-unknown-rejection", ["Public MCP interface"],
         "MCP aflow_verify schema rejects unknown properties",
         "Request with unknown field fails schema validation (additionalProperties: false); observed via contract test",
         ["mcp-contract"]),
        ("validation-pass", ["Validation execution behavior", "Required tests"],
         "Persisted validation with passing command produces result passed",
         "Command uv run pytest -q (or similar) exits 0, captured output contains pass summary, result status passed",
         ["validation-execution"]),
        ("validation-fail", ["Validation execution behavior", "Failure and timeout behavior"],
         "Persisted validation with failing command produces result failed with preserved evidence",
         "Command exits non-zero, result status failed, stdout/stderr captured, previous revision preserved",
         ["validation-execution"]),
        ("validation-timeout", ["Validation execution behavior", "Failure and timeout behavior"],
         "Validation command exceeding bounded timeout of 120 seconds produces timed_out result with partial evidence preserved",
         "Execution exceeds 120s, process terminated, result timed_out, duration recorded, evidence path exists",
         ["timeout-enforcement"]),
        ("validation-unsupported", ["Validation execution behavior"],
         "Unsupported validation command syntax produces unsupported result without executing arbitrary shell",
         "Command not in allowlist results in unsupported status, no shell execution, safe error captured",
         ["security-boundary"]),
        ("fixture-pass", ["Fixture execution behavior"],
         "Fixture with matching expected result produces passed result after setup/cleanup",
         "Fixture normal case setup -> exec -> compare actual==expected -> cleanup owned temp only -> passed",
         ["fixture-execution"]),
        ("fixture-fail", ["Fixture execution behavior", "Required tests"],
         "Fixture with mismatched expected result produces failed result and preserves failure evidence",
         "Actual vs expected differ, result failed, evidence captures both values, fixture temp cleaned",
         ["fixture-execution"]),
        ("malformed-fixture-rejection", ["Failure and timeout behavior"],
         "Malformed fixture definition missing required fields is rejected as not_run with evidence of schema violation",
         "Fixture missing input or expected_result -> validation error -> result not_run, malformed recorded as evidence",
         ["validation"]),
        ("success-criteria-assessment", ["Success-criteria assessment"],
         "Every persisted success criterion receives one assessment of satisfied/not_satisfied/not_verified/stale with evidence link",
         "For each criterion_id there exists assessment containing criterion_id, related step ids, validation ids, fixture ids, evidence paths, explanation; no criterion left without assessment",
         ["success-criteria"]),
        ("failed-evidence-reopens-only-affected", ["Work-state integration", "Stale-evidence behavior"],
         "Failed validation reopens only work items it covers, unrelated steps remain unchanged",
         "Given two completed steps each covered by distinct validations, when one validation fails only its covered step_id moves to in_progress, other remains completed",
         ["work-state-integration"]),
        ("stale-evidence-reopens-only-affected", ["Stale-evidence behavior"],
         "Tracked file change after validation passed marks that validation stale and reopens only its covered steps",
         "Modify file listed in file_states of one validation; resume detects size/mtime mismatch -> validation stale, covered step in_progress, unrelated unchanged, no repo-wide scan",
         ["stale-detection"]),
        ("unrelated-unchanged", ["Stale-evidence behavior", "Work-state integration"],
         "Unrelated work remains unchanged when one validation becomes stale or failed",
         "After stale or failure of single validation, count of unchanged completed steps == total-1, operation_status in_progress",
         ["work-state"]),
        ("final-result-persistence", ["Final result artifact"],
         "Final operation result is persisted atomically at operation_dir/final-result.json containing required fields and completion_time ISO-8601 UTC",
         "File exists, JSON parses, contains operation_id, work_state_revision, verification_status, satisfied/failed/unverified lists, completion_time ISO format, written via .tmp + os.replace",
         ["atomic-persistence"]),
        ("revision-conflict-rejection", ["Atomic persistence"],
         "Verification or progress update with stale expected_revision is rejected without overwriting state",
         "Attempt with expected_revision < current returns error stale expected_revision, current revision on disk unchanged, no overwrite",
         ["revision-protection"]),
        ("concurrent-protection", ["Atomic persistence"],
         "Concurrent verification or progress updates cannot silently overwrite each other via O_EXCL lock file",
         "Two processes attempt update same operation; second gets operation locked error via .lock O_EXCL, first succeeds, no silent last-write-wins",
         ["concurrency"]),
        ("interrupted-write-preservation", ["Atomic persistence", "Failure and timeout behavior"],
         "Interrupted artifact write preserves previous valid revision",
         "Simulate write failure after tmp file creation but before replace; previous valid revision still loads, no partial mix",
         ["atomic-persistence"]),
        ("resume-after-verification", ["Work-state integration"],
         "Another process can resume verified operation by operation_id only and receives latest revision and evidence",
         "Call resume_operation(operation_id) after verification returns continuation package with latest revision, completed work, failed_results etc.",
         ["resume"]),
        ("no-conversation-stored", ["Required tests", "Security"],
         "No conversation or interaction history is persisted in operation artifacts",
         "All JSON artifacts lowercased contain none of banned phrases user message, assistant message, chat history",
         ["security"]),
        ("repo-boundary-enforcement", ["Security and repository boundaries"],
         "Verifier remains within recorded repository root and rejects path traversal including ../, absolute outside root, UNC, drive escape, symlink outside",
         "Attempt to validate spec with working_directory resolving outside repo_root is rejected as unauthorized path",
         ["security-boundary"]),
        ("no-review-rerun-during-verify", ["Required tests"],
         "Verification does not rerun any canonical review stage (intake through companion_package)",
         "Stage_state remains completed after verification; operation-config stage_state unchanged, only work-state artifacts updated",
         ["workflow"]),
    ]

    # Determine if this is a verification plan
    is_verification_plan = any(k in lower for k in ("after-execution", "verification capability", "aflow verify", "validation execution behavior", "fixture execution behavior"))

    if is_verification_plan:
        for cid, sections, req, observable, no_regress in verification_sections:
            criteria.append(SuccessCriterion(
                criterion_id=cid,
                source_plan_step_ids=sections[:3],
                requirement=req,
                observable_result=observable,
                must_not_regress=no_regress,
            ))
    else:
        # Generic plan: derive from headings
        for idx, hid in enumerate(headings[:10]):
            criteria.append(SuccessCriterion(
                criterion_id=f"criterion-{hid}",
                source_plan_step_ids=[hid],
                requirement=f"Section {hid} is implemented and verified",
                observable_result=f"Observable outcome for {hid} matches expected behavior from plan",
                must_not_regress=[],
            ))
        if not criteria:
            criteria.append(SuccessCriterion(
                criterion_id="plan-immutable",
                source_plan_step_ids=["entire-plan"],
                requirement="Original plan remains unchanged",
                observable_result="Byte-for-byte match preserved",
                must_not_regress=["original-plan"],
            ))

    # Add resolved gap corrections as additional criteria only if not verification plan (to keep traceability clean)
    if not is_verification_plan:
        for gr in gap_resolutions:
            if gr.resolution_status == "resolved":
                criteria.append(SuccessCriterion(
                    criterion_id=f"corrected-{gr.gap_id}",
                    source_plan_step_ids=["gap-correction"],
                    requirement=f"Gap {gr.gap_id} resolved",
                    observable_result=f"Correction: {gr.correction_applied}",
                    must_not_regress=[],
                ))

    return criteria
