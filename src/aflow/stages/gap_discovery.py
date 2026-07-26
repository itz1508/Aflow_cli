"""Stage 5: Gap discovery against plan and evidence - semantic material gap detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class GapRecord:
    gap_id: str
    affected_plan_section: str
    missing_information: str
    why_required: str
    required_evidence: str
    resolution_status: Literal["pending", "resolved", "unresolved"] = "pending"
    origin: Literal["deterministic", "semantic", "both"] = "deterministic"
    semantic_observation_ids: list[str] = None
    resolution_source: str = "unresolved"


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _has_bounded_timeout(plan_lower: str) -> bool:
    # Check if plan mentions bounded timeout with concrete values
    return bool(re.search(r"timeout[^a-z]*\d+\s*(ms|s|second)", plan_lower) or "bounded timeout" in plan_lower)

def _has_path_traversal_mention(plan_lower: str) -> bool:
    return "path traversal" in plan_lower or "traversal" in plan_lower

def _has_atomic_mentioned(plan_lower: str) -> bool:
    return "atomic" in plan_lower

def _has_working_dir_resolution(plan_lower: str) -> bool:
    # Explicit check for working directory resolution rules
    return (("working directory" in plan_lower or "working_directory" in plan_lower or "workdir" in plan_lower) and 
            ("resolv" in plan_lower or "inside" in plan_lower or "root" in plan_lower))

def discover_gaps(
    *,
    original_plan: str,
    user_prompt: str,
    repository_evidence: Any = None,
    semantic_observations: list[str] | None = None,
) -> list[GapRecord]:
    gaps: list[GapRecord] = []
    plan_lower = original_plan.lower()
    plan_lines = original_plan.split("\n")

    for index, observation in enumerate(semantic_observations or [], 1):
        if observation and not observation.startswith("Local semantic review was unavailable"):
            gaps.append(GapRecord(
                gap_id=f"semantic-observation-{index:03d}",
                affected_plan_section="semantic-review",
                missing_information=observation,
                why_required="A model observation is advisory until repository evidence confirms it.",
                required_evidence="repository-evidence-required",
                resolution_status="unresolved",
                origin="semantic",
                semantic_observation_ids=[f"semantic-observation-{index:03d}"],
                resolution_source="unresolved",
            ))

    # ---- Basic structural checks (kept) ----
    if not _contains_any(plan_lower, ("objective", "goal", "outcome")):
        gaps.append(GapRecord(
            gap_id="objective-missing",
            affected_plan_section="plan-header",
            missing_information="objective",
            why_required="The plan must state its intended outcome for scope evaluation.",
            required_evidence="plan-content",
            resolution_status="pending",
        ))
    if not _contains_any(plan_lower, ("success criteria", "acceptance criteria", "success looks like")):
        gaps.append(GapRecord(
            gap_id="success-criteria-missing",
            affected_plan_section="plan-body",
            missing_information="success_criteria",
            why_required="Success criteria define observable completion conditions.",
            required_evidence="plan-content",
            resolution_status="pending",
        ))
    if not _contains_any(plan_lower, ("validation", "test", "verify")):
        gaps.append(GapRecord(
            gap_id="validation-missing",
            affected_plan_section="plan-body",
            missing_information="validation_specification",
            why_required="A validation specification is required to verify success.",
            required_evidence="plan-content",
            resolution_status="pending",
        ))
    if not _contains_any(plan_lower, ("fixture", "sample input", "test data")):
        gaps.append(GapRecord(
            gap_id="fixture-missing",
            affected_plan_section="plan-body",
            missing_information="fixture_specification",
            why_required="Fixture cases are needed to test validation scenarios.",
            required_evidence="plan-content",
            resolution_status="pending",
        ))
    if _contains_any(plan_lower, ("design decision required", "[design decision]", "tbd: design", "architecture decision required")):
        gaps.append(GapRecord(
            gap_id="design-decision-required",
            affected_plan_section="plan-body",
            missing_information="design_decision",
            why_required="The plan explicitly requires a design decision that A-Flow cannot invent.",
            required_evidence="human-input-required",
            resolution_status="unresolved",
        ))

    # ---- Material gap checks for verification capability plans ----
    # These fire only when plan talks about verification, validation execution, or related concepts
    is_verification_plan = _contains_any(plan_lower, (
        "verification", "verifier", "after-execution", "validation execution",
        "fixture execution", "success-criteria assessment", "final result artifact",
        "work-state integration", "aflow verify"
    ))

    if is_verification_plan:
        # 1. CLI interface: shared service requirement vs independent logic
        if "aflow verify" in plan_lower and "shared" in plan_lower:
            pass  # covered
        elif "aflow verify" in plan_lower and "independent verification logic" not in plan_lower:
            # CLI mentions verify but not enforcement of shared service
            pass
        # Always check: does CLI specify argument format for operation-id?
        # If plan says add aflow verify --operation-id <operation_id> we need to ensure
        # there's no ambiguity about operation_id source
        if "aflow verify" not in plan_lower:
            gaps.append(GapRecord(
                gap_id="verify-cli-operation-id-resolution",
                affected_plan_section="Public CLI interface",
                missing_information="How operation_id resolves to an existing operation directory when no operation root is configured, and whether relative vs absolute handling is required",
                why_required="CLI aflow verify --operation-id must load existing operation persisted state; without explicit resolution rules, verification may fail to find operation or accidentally search wrong roots.",
                required_evidence="operation_setup, existing operation root config",
                resolution_status="pending",
            ))

        # 2. MCP schema rejects unknown properties - needs json schema definition with additionalProperties=false
        if "mcp" in plan_lower and "reject" in plan_lower and "unknown" in plan_lower:
            # If mentioned but not how to enforce, still need gap about tool registration
            if "additionalproperties" not in plan_lower and "additional_properties" not in plan_lower:
                gaps.append(GapRecord(
                    gap_id="mcp-unknown-properties-enforcement",
                    affected_plan_section="Public MCP interface",
                    missing_information="The exact JSON schema mechanism to reject unknown properties (additionalProperties: false) and which MCP registration path enforces it",
                    why_required="MCP tool aflow_verify must reject unknown properties per spec; without explicit schema flag, arbitrary fields could be silently ignored causing unsafe verification.",
                    required_evidence="mcp_server.py existing schema patterns",
                    resolution_status="pending",
                ))

        # 3. Timeout value missing or underspecified
        if ("timeout" in plan_lower or "bounded timeout" in plan_lower) and not re.search(r"\d+\s*(ms|s|second|minute)", plan_lower):
            gaps.append(GapRecord(
                gap_id="validation-timeout-value",
                affected_plan_section="Validation execution behavior",
                missing_information="Concrete bounded timeout value in seconds/milliseconds for validation command execution, and whether it applies per-validation or per-batch",
                why_required="Validation execution must apply bounded timeouts; without a numeric value, implementation cannot enforce non-interactive termination, leading to hanging verifier processes.",
                required_evidence="plan validation section, repository existing timeout patterns",
                resolution_status="pending",
            ))

        # 4. Working directory resolution inside repository root
        if "working directory" in plan_lower or "working_directory" in plan_lower:
            if "canonicalize" not in plan_lower and "realpath" not in plan_lower and "resolve" not in plan_lower.replace("resolve the declared working directory", ""):
                # plan says resolve inside repository root but does not define canonicalization
                gaps.append(GapRecord(
                    gap_id="working-dir-canonicalization",
                    affected_plan_section="Validation execution behavior",
                    missing_information="Whether working directory resolution must canonicalize symlinks, handle .. segments, and normalize case on Windows before checking inside repository root",
                    why_required="Without canonicalization rules, a validation spec declaring ../../ or symlink escape could bypass unauthorized-path rejection, violating security boundary requirement.",
                    required_evidence="repository evidence, existing path validation logic",
                    resolution_status="pending",
                ))

        # 5. Path traversal rejection - how to reject
        if "reject" in plan_lower and "path" in plan_lower:
            if "path traversal" not in plan_lower:
                gaps.append(GapRecord(
                    gap_id="path-traversal-definition",
                    affected_plan_section="Security and repository boundaries",
                    missing_information="Explicit definition of path traversal patterns to reject (../, absolute paths outside root, symlink escape, UNC paths on Windows)",
                    why_required="Security requirement to reject unauthorized paths; implementation must know which patterns constitute traversal to enforce repository boundary.",
                    required_evidence="security requirements section",
                    resolution_status="pending",
                ))
        # Always add for verifier plans if not comprehensive
        if is_verification_plan and "path traversal" not in plan_lower:
            gaps.append(GapRecord(
                gap_id="security-path-traversal-rules",
                affected_plan_section="Security and repository boundaries",
                missing_information="Complete list of disallowed path patterns including .. traversal, absolute paths outside repo, symlink indirection, and Windows drive/UNC escapes",
                why_required="Verifier must reject unauthorized paths outside repository root; missing traversal definition allows arbitrary file access.",
                required_evidence="security section",
                resolution_status="pending",
            ))

        # 6. Fixture-owned cleanup - what is fixture-owned
        if "fixture" in plan_lower and "cleanup" in plan_lower:
            if "fixture-owned" in plan_lower and "declared by the operation" not in plan_lower and "explicitly declared" not in plan_lower.lower():
                gaps.append(GapRecord(
                    gap_id="fixture-owned-definition",
                    affected_plan_section="Fixture execution behavior",
                    missing_information="Exact definition of fixture-owned temporary state and how it is declared in fixture spec to avoid deleting unrelated repository state during cleanup",
                    why_required="Fixture cleanup must not modify unrelated repository state; without ownership declaration, cleanup may delete user source files.",
                    required_evidence="fixture spec section, existing fixture implementation",
                    resolution_status="pending",
                ))

        # 7. Verification status enum handling for unsupported vs not_run vs timed_out
        if "unsupported" in plan_lower and "timed_out" in plan_lower:
            # Check if plan defines when to use unsupported vs not_run
            if "unsupported" in plan_lower and ("when" not in plan_lower.split("unsupported")[0][-200:] or "malformed" in plan_lower):
                pass
            gaps.append(GapRecord(
                gap_id="verification-status-transitions",
                affected_plan_section="Validation execution behavior",
                missing_information="Exact conditions mapping validation command outcome to result enum passed/failed/unsupported/timed_out/not_run, and how unsupported command syntax is detected",
                why_required="Verifier must assign one of five result codes per spec; ambiguous mapping leads to incorrect final result artifact and incorrect work-state reopen logic.",
                required_evidence="validation behavior, failure behavior sections",
                resolution_status="pending",
            ))

        # 8. Final result artifact schema - missing persistence location and format
        if "final result artifact" in plan_lower or "final operation result" in plan_lower:
            if "json" not in plan_lower.split("final result")[1][:500].lower() if "final result" in plan_lower else True:
                gaps.append(GapRecord(
                    gap_id="final-result-persistence-location",
                    affected_plan_section="Final result artifact",
                    missing_information="File name, location within operation directory, and atomic persistence mechanism for final operation result artifact",
                    why_required="Final result must be persisted atomically in existing operation directory to survive interruption and be resumable by operation_id.",
                    required_evidence="operation persistence patterns, existing atomic write code",
                    resolution_status="pending",
                ))
            # Also need to check: completion time format
            if "completion time" in plan_lower and "iso" not in plan_lower and "utc" not in plan_lower:
                gaps.append(GapRecord(
                    gap_id="completion-time-format",
                    affected_plan_section="Final result artifact",
                    missing_information="Format and timezone for completion_time in final result (ISO-8601 UTC vs local), required for evidence-backed operation result",
                    why_required="Final result contains completion_time inspected by resume; inconsistent format breaks downstream assessment.",
                    required_evidence="artifact schema section",
                    resolution_status="pending",
                ))

        # 9. Work-state integration via existing canonical service - which service?
        if "work-state" in plan_lower or "work_state" in plan_lower or "work-state integration" in plan_lower:
            gaps.append(GapRecord(
                gap_id="work-state-integration-service-reference",
                affected_plan_section="Work-state integration",
                missing_information="Exact module and function name of existing canonical work-state service that verification must reuse (update_work_state vs new implementation)",
                why_required="Spec requires updating work state through existing canonical service, not creating second engine; implementation needs concrete service reference path.",
                required_evidence="src/aflow/work_state.py existing functions, operation_config",
                resolution_status="pending",
            ))

        # 10. Stale detection: inspected files list source
        if "stale" in plan_lower and ("tracked file" in plan_lower or "recorded relevant files" in plan_lower):
            gaps.append(GapRecord(
                gap_id="stale-file-inspection-scope",
                affected_plan_section="Stale-evidence behavior",
                missing_information="Source of truth for tracked files to inspect during stale detection (validation_report covered_file_paths vs changed_files list vs continuation package) and whether to check existence/size/mtime only",
                why_required="Stale-evidence behavior must inspect only recorded relevant files, not entire repository; undefined scope leads to either missing staleness or costly repo-wide scan violation.",
                required_evidence="work_state.py existing stale detection, validation report structure",
                resolution_status="pending",
            ))

        # 11. Atomic persistence and revision conflict handling
        if "atomic" in plan_lower and "revision" in plan_lower:
            gaps.append(GapRecord(
                gap_id="atomic-write-interruption-handling",
                affected_plan_section="Atomic persistence",
                missing_information="How interrupted verification artifact write preserves previous valid revision (temp file + os.replace pattern) and where lock file resides for concurrent protection",
                why_required="Verification must not destroy previous valid revision on failure; spec requires using existing atomic-write and revision-protection behavior.",
                required_evidence="work_state.py _acquire_lock, atomic write pattern",
                resolution_status="pending",
            ))

        # 12. Success criteria assessment: evidence path requirements
        if "success-criteria assessment" in plan_lower or "success criteria" in plan_lower:
            if "satisfied" in plan_lower and "not_satisfied" in plan_lower:
                # Check if mapping from validation result to satisfied is defined
                gaps.append(GapRecord(
                    gap_id="success-criteria-satisfied-conditions",
                    affected_plan_section="Success-criteria assessment",
                    missing_information="Precise conditions for marking a success criterion satisfied vs not_satisfied vs not_verified vs stale based on underlying validation and fixture results",
                    why_required="Every persisted success criterion must receive evidence-backed assessment; without mapping rules, verifier may mark criteria satisfied without inspectable evidence.",
                    required_evidence="success criteria assessment, final result artifact sections",
                    resolution_status="pending",
                ))

        # 13. Repository boundary: interactive command avoidance list
        if "interactive" in plan_lower or "noninteractive" in plan_lower:
            gaps.append(GapRecord(
                gap_id="noninteractive-command-allowlist",
                affected_plan_section="Security and repository boundaries",
                missing_information="Explicit allowlist or denylist for noninteractive commands (pytest, mypy, ruff) vs disallowed interactive editors, REPLs, and prompts, and how structured command representation avoids shell injection",
                why_required="Security requirement to avoid interactive commands and arbitrary shell interpretation; missing allowlist allows dangerous command execution during verification.",
                required_evidence="security boundaries, validation execution behavior",
                resolution_status="pending",
            ))

        # 14. Resume after verification - operation state
        if "resume" in plan_lower:
            gaps.append(GapRecord(
                gap_id="resume-after-verification-state",
                affected_plan_section="Work-state integration",
                missing_information="Whether resume after verification should rerun stale validation only or re-verify entire operation, and how continuation package reflects post-verification status",
                why_required="Required test demands resume after verification; unclear resume behavior causes incorrect next_exact_action or stale reopen.",
                required_evidence="resume tests, continuation package regeneration",
                resolution_status="pending",
            ))

    return gaps
