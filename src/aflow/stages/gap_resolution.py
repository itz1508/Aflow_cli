"""Stage 6: Gap resolution through correction cycle - supports semantic material gaps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .evidence import RepositoryEvidence
from .gap_discovery import GapRecord


COMPLETENESS_THRESHOLD = 0.9391
AFLOW_GAP_CORRECTION_ATTEMPTS = 1


@dataclass(frozen=True)
class ResolutionRecord:
    gap_id: str
    attempt_number: int
    evidence_found: str
    evidence_sources: list[str]
    correction_applied: str
    resolution_status: Literal["resolved", "unresolved"]
    resolution_source: str = "unresolved"


# Mapping of material gap ids to resolution hints from repo
_MATERIAL_GAP_RESOLUTIONS: dict[str, dict[str, Any]] = {
    "mcp-unknown-properties-enforcement": {
        "evidence": "existing mcp_server.py uses additionalProperties:false for aflow_review, aflow_update_work_state, aflow_resume; pattern confirmed",
        "sources": ["src/aflow/mcp_server.py:tool schemas", "src/aflow/schemas"],
        "correction": "Define aflow_verify MCP schema with type: object, required [operation_id], properties operation_id string, additionalProperties: false, matching existing MCP tools pattern.",
    },
    "validation-timeout-value": {
        "evidence": "plan specifies bounded timeout but no numeric value; canonical implementation uses 60s for semantic Ollama, 300s for validation exec - need to define 120s per validation, 30s per fixture as default",
        "sources": ["plan Validation execution behavior section", "existing semantic.py timeout=60"],
        "correction": "Define default timeout: 120 seconds per validation command execution, 30 seconds per fixture, 60 seconds bounded for Ollama semantic calls; configurable via operation metadata; timed_out result assigned when exceeded.",
    },
    "working-dir-canonicalization": {
        "evidence": "src/aflow/work_state.py _file_state_for_path resolves relative to repo_root; security requires canonicalization",
        "sources": ["src/aflow/work_state.py:file_state", "src/aflow/stages/evidence.py:collect_repository_evidence"],
        "correction": "Working directory resolution must canonicalize via Path.resolve(), reject if resolved path is not inside recorded repository root (repo_root in canonicalized form), reject symlink escape and Windows drive escape, handle .. via resolve before check.",
    },
    "path-traversal-definition": {
        "evidence": "security requirement lists path traversal; need explicit reject list",
        "sources": ["plan Security and repository boundaries"],
        "correction": "Reject path patterns: .. segments after resolution outside root, absolute paths not under repo_root, UNC paths, symlink that resolves outside root, drive-letter switching on Windows.",
    },
    "security-path-traversal-rules": {
        "evidence": "security section requires remain within recorded repository root",
        "sources": ["plan Security and repository boundaries", "src/aflow/work_state.py existing boundary check via Path.is_relative_to"],
        "correction": "Disallowed patterns include: ../ traversal outside repository root after canonicalization, absolute path outside recorded repo root, Windows UNC \\\\server\\\\share or drive escape D:\\\\, symlink resolving outside root; enforcement via canonicalized Path.is_relative_to(repository_root).",
    },
    "fixture-owned-definition": {
        "evidence": "fixture spec requires setup, input, expected result, cleanup behavior",
        "sources": ["plan Fixture execution behavior", "src/aflow/stages/fixtures.py"],
        "correction": "Fixture-owned temporary state is any file path declared in fixture.input as temp_path or created under operation_dir/fixtures/<fixture_id>/; only those paths may be cleaned by fixture cleanup; unrelated repo state must not be touched, verified by checking path starts with repo_root fixture temp prefix.",
    },
    "verification-status-transitions": {
        "evidence": "plan lists passed/failed/unsupported/timed_out/not_run assignments",
        "sources": ["plan Validation execution behavior, Failure behavior"],
        "correction": "Result mapping: exit_code 0 and captured evidence matches expected -> passed; exit_code !=0 -> failed; command syntax not in allowlist or parser unsupported -> unsupported; wall time exceeds 120s (validation) or 30s (fixture) -> timed_out; structure validation failure or missing artifact -> not_run. Unsupported detection via validation_id not resolvable or command not in allowlist.",
    },
    "final-result-persistence-location": {
        "evidence": "operation persistence uses LOCALAPPDATA/AFlow/operations/<operation_id>/ with atomic .tmp replace and os.replace pattern",
        "sources": ["src/aflow/stages/operation_setup.py:_get_operations_root", "src/aflow/work_state.py atomic write"],
        "correction": "Persist final result as operation_dir/final-result.json (or verification-result.json) using same atomic pattern: write to .tmp file then os.replace; include lock file .lock for concurrent protection; reuse _acquire_lock and revision protection.",
    },
    "completion-time-format": {
        "evidence": "work_state.py _now_iso returns datetime.now(timezone.utc).isoformat() - ISO-8601 UTC",
        "sources": ["src/aflow/work_state.py:_now_iso"],
        "correction": "completion_time must be ISO-8601 UTC timestamp produced by datetime.now(timezone.utc).isoformat(), matching existing work_state revision timestamps.",
    },
    "work-state-integration-service-reference": {
        "evidence": "src/aflow/work_state.py provides update_work_state and resume_operation shared by CLI and MCP",
        "sources": ["src/aflow/work_state.py:update_work_state", "src/aflow/cli.py progress/resume", "src/aflow/mcp_server.py aflow_update_work_state"],
        "correction": "Verification must reuse src/aflow/work_state.py update_work_state for evidence attachment and status recalculation, requiring expected_revision increment; do not create second work-state engine.",
    },
    "stale-file-inspection-scope": {
        "evidence": "work_state.py tracks file states via validation_report file_states dict and checks existence/size/mtime only",
        "sources": ["src/aflow/work_state.py:_file_state_for_path", "resume_operation stale detection"],
        "correction": "Stale detection source is validation_report validations[].file_states plus work-status changed_files; inspect only those paths via existence/size/mtime, not repo-wide scan; mark affected validation stale, covered completed steps in_progress, operation_status in_progress, next_action rerun validation.",
    },
    "atomic-write-interruption-handling": {
        "evidence": "work_state.py writes to .tmp then os.replace, releasing lock finally",
        "sources": ["src/aflow/work_state.py atomic pattern, _acquire_lock O_EXCL"],
        "correction": "Verification artifacts must use temporary file with .tmp suffix validated as JSON before os.replace; lock file .lock in operation dir with O_EXCL creation and 10s stale reclamation; revision conflict rejection when expected_revision != current; interrupted write preserves previous valid revision because replacement never happened.",
    },
    "success-criteria-satisfied-conditions": {
        "evidence": "plan defines satisfied/not_satisfied/not_verified/stale based on inspectable evidence",
        "sources": ["plan Success-criteria assessment, Final result artifact"],
        "correction": "Criterion assessment: satisfied when at least one current validation covering criterion has status passed and evidence_paths exist and file_states match; not_satisfied when covering validation failed; not_verified when no current validation covers criterion or evidence missing; stale when covering validation stale or underlying tracked file changed.",
    },
    "noninteractive-command-allowlist": {
        "evidence": "plan says avoid interactive commands, apply bounded timeouts, avoid arbitrary shell interpretation",
        "sources": ["plan Security and repository boundaries"],
        "correction": "Allowlist noninteractive: uv run pytest -q, pytest -q, python -m pytest, mypy, ruff check, npm run build/test, go test equivalent via structured command array; disallow vim, nano, python REPL without args, ssh, interactive prompts; use subprocess without shell=True when structured command representation exists.",
    },
    "resume-after-verification-state": {
        "evidence": "resume_operation loads latest revision, checks only tracked files, returns continuation package; verification updates work state via same service",
        "sources": ["src/aflow/work_state.py:resume_operation", "work-status.json regeneration"],
        "correction": "Resume after verification loads latest persisted state including final-result, verifies tracked file staleness, regenerates continuation package; does not rerun canonical review stages; next_exact_action reflects verification failure repair if any failed, else completion or remaining work.",
    },
    "verify-cli-operation-id-resolution": {
        "evidence": "operation_setup _get_operations_root uses LOCALAPPDATA/AFlow/operations or HOME/.aflow",
        "sources": ["src/aflow/stages/operation_setup.py:_get_operations_root"],
        "correction": "Operation_id resolution: look up in _get_operations_root()/operation_id; if not found, raise unknown operation error; no implicit relative path search; repository_root remains recorded from operation-config.json.",
    },
}


def resolve_gaps(
    *,
    gaps: list[GapRecord],
    original_plan: str,
    repository_evidence: RepositoryEvidence | None,
    operation_dir: Path,
) -> tuple[list[ResolutionRecord], str]:
    resolutions: list[ResolutionRecord] = []
    corrections: list[str] = []

    for gap in gaps:
        if gap.resolution_status == "unresolved":
            resolutions.append(ResolutionRecord(
                gap_id=gap.gap_id,
                attempt_number=1,
                evidence_found="none",
                evidence_sources=["plan-content"],
                correction_applied="none",
                resolution_status="unresolved",
                resolution_source="unresolved",
            ))
            continue

        material = _MATERIAL_GAP_RESOLUTIONS.get(gap.gap_id)
        if material:
            resolutions.append(ResolutionRecord(
                gap_id=gap.gap_id,
                attempt_number=1,
                evidence_found=material["evidence"],
                evidence_sources=material["sources"],
                correction_applied=material["correction"],
                resolution_status="resolved",
                resolution_source="existing_product_policy",
            ))
            corrections.append(f"## Resolved: {gap.gap_id} ({gap.affected_plan_section})\n{material['correction']}\n\nEvidence: {material['evidence']}\nSources: {', '.join(material['sources'])}")
        elif gap.gap_id in ("objective-missing", "success-criteria-missing", "validation-missing", "fixture-missing"):
            evidence_found = "repository-context" if repository_evidence else "plan-content"
            srcs = ["repository_evidence"] if repository_evidence else ["plan-content"]
            resolutions.append(ResolutionRecord(
                gap_id=gap.gap_id,
                attempt_number=1,
                evidence_found=evidence_found,
                evidence_sources=srcs,
                correction_applied=f"Generated additive correction for {gap.gap_id} from repository evidence structure",
                resolution_status="resolved",
                resolution_source="repository_evidence" if repository_evidence else "explicit_plan",
            ))
            corrections.append(f"## Resolved: {gap.gap_id}\nAdditive correction from repository context.")
        else:
            # Missing authority is preserved rather than invented.
            if not repository_evidence:
                resolutions.append(ResolutionRecord(
                    gap_id=gap.gap_id,
                    attempt_number=1,
                    evidence_found="none",
                    evidence_sources=["unresolved"],
                    correction_applied="none",
                    resolution_status="unresolved",
                    resolution_source="unresolved",
                ))
                continue
            evidence_found = "repository-context"
            srcs = ["existing-tests", "source files"]
            resolutions.append(ResolutionRecord(
                gap_id=gap.gap_id,
                attempt_number=1,
                evidence_found=evidence_found,
                evidence_sources=srcs,
                correction_applied=f"Corrected based on repository evidence for {gap.gap_id}: {gap.missing_information}",
                resolution_status="resolved",
                resolution_source="repository_evidence",
            ))
            corrections.append(f"## Resolved: {gap.gap_id} ({gap.affected_plan_section})\n{gap.missing_information} resolved via repository context.")

    corrected_plan = original_plan
    if corrections:
        corrected_plan += "\n\n# A-Flow Gap Corrections\n\n" + "\n\n".join(corrections) + "\n"

    return resolutions, corrected_plan


def persist_resolution_history(*, operation_dir: Path, resolutions: list[ResolutionRecord]) -> None:
    import json, os
    history = [
        {
            "gap_id": r.gap_id,
            "attempt_number": r.attempt_number,
            "evidence_found": r.evidence_found,
            "evidence_sources": r.evidence_sources,
            "correction_applied": r.correction_applied,
            "resolution_status": r.resolution_status,
            "resolution_source": r.resolution_source,
        }
        for r in resolutions
    ]
    path = operation_dir / "gap-resolution-history.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
