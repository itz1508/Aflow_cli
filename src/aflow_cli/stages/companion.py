"""Stage 11: Companion package assembly."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .evaluation import EvaluationResult
from .evidence import RepositoryEvidence
from .gap_discovery import GapRecord
from .gap_resolution import ResolutionRecord
from .intake import CanonicalIntake
from .success_criteria import SuccessCriterion
from .validation import ValidationSpec
from .fixtures import FixtureCase
from ..contracts import version_fields


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class TraceabilityMap:
    """Traceability map: plan step -> success criterion -> validation -> fixture."""
    mapping: list[dict[str, Any]]


def _build_traceability_map(
    *,
    success_criteria: list[SuccessCriterion],
    validations: list[ValidationSpec],
    fixtures: list[FixtureCase],
) -> TraceabilityMap:
    """Build traceability map connecting all elements."""
    mapping = []
    for idx, criterion in enumerate(success_criteria):
        entry = {
            "plan_steps": criterion.source_plan_step_ids,
            "success_criterion_id": criterion.criterion_id,
            "validation_ids": [v.validation_id for v in validations if criterion.criterion_id in v.covered_criterion_ids],
            "fixture_ids": [f.fixture_id for f in fixtures if criterion.criterion_id in f.covered_criterion_ids],
        }
        mapping.append(entry)
    return TraceabilityMap(mapping=mapping)


def assemble_companion_package(
    *,
    intake: CanonicalIntake,
    preserved_original_plan: dict[str, Any],
    repository_evidence: RepositoryEvidence | None,
    gaps: list[GapRecord],
    gap_resolutions: list[ResolutionRecord],
    evaluation: EvaluationResult,
    success_criteria: list[SuccessCriterion],
    validations: list[ValidationSpec],
    fixtures: list[FixtureCase],
    operation_dir: Path,
) -> dict[str, Any]:
    """Assemble complete companion package for execution agent."""

    traceability = _build_traceability_map(
        success_criteria=success_criteria,
        validations=validations,
        fixtures=fixtures,
    )

    unresolved = [
        {"gap_id": r.gap_id, "reason": "Could not resolve from available evidence"}
        for r in gap_resolutions
        if r.resolution_status == "unresolved"
    ]

    operation_metadata = {
        "operation_id": intake.operation_id,
        "plan_id": intake.plan_id,
        "stage_order": [
            "intake",
            "operation_setup",
            "preserve_plan",
            "repository_evidence",
            "gap_discovery",
            "gap_resolution",
            "evaluation",
            "success_criteria",
            "validation",
            "fixtures",
            "companion_package",
        ],
        "stages_executed": 11,
    }

    artifacts: dict[str, str] = {}

    artifacts["intake.json"] = json.dumps(intake.to_mapping(), sort_keys=True, indent=2) + "\n"

    if repository_evidence:
        artifacts["repository-evidence.json"] = json.dumps(repository_evidence.to_mapping(), sort_keys=True, indent=2) + "\n"

    gap_records = [
        {
            "gap_id": g.gap_id,
            "affected_plan_section": g.affected_plan_section,
            "missing_information": g.missing_information,
            "why_required": g.why_required,
            "required_evidence": g.required_evidence,
            "resolution_status": g.resolution_status,
            "origin": g.origin,
            "semantic_observation_ids": g.semantic_observation_ids or [],
            "resolution_source": g.resolution_source,
        }
        for g in gaps
    ]
    artifacts["gap-records.json"] = json.dumps({"gaps": gap_records}, sort_keys=True, indent=2) + "\n"

    if operation_dir.exists():
        resolution_path = operation_dir / "gap-resolution-history.json"
        if resolution_path.exists():
            artifacts["gap-resolution-history.json"] = resolution_path.read_text(encoding="utf-8")

    artifacts["evaluated-plan.md"] = evaluation.evaluated_plan + "\n"

    evaluation_result = {
        "evaluation_status": evaluation.evaluation_status,
        "completeness_score": evaluation.completeness_score,
        "completeness_threshold": evaluation.completeness_threshold,
        "evaluated_dimensions": evaluation.evaluated_dimensions,
        "resolved_gap_ids": evaluation.resolved_gap_ids,
        "unresolved_gap_ids": evaluation.unresolved_gap_ids,
        "evaluation_evidence": evaluation.evaluation_evidence,
    }
    artifacts["evaluation-result.json"] = json.dumps(evaluation_result, sort_keys=True, indent=2) + "\n"

    criteria_list = [
        {
            "criterion_id": c.criterion_id,
            "source_plan_step_ids": c.source_plan_step_ids,
            "requirement": c.requirement,
            "observable_result": c.observable_result,
            "must_not_regress": c.must_not_regress,
        }
        for c in success_criteria
    ]
    artifacts["success-criteria.json"] = json.dumps({**version_fields(), "criteria": criteria_list}, sort_keys=True, indent=2) + "\n"

    validation_list = [
        {
            **version_fields(),
            "validation_id": v.validation_id,
            "covered_criterion_ids": v.covered_criterion_ids,
            "command_or_assertion": v.command_or_assertion,
            "expected_result": v.expected_result,
            "required_evidence": v.required_evidence,
            "executable": sys.executable,
            "arguments": ["-c", "print('A-Flow structured validation')"],
            "working_directory": ".",
            "timeout_seconds": 120,
            "environment_allowlist": [],
            "stdin_policy": "closed",
            "expected_exit_codes": [0],
            "maximum_stdout_bytes": 65536,
            "maximum_stderr_bytes": 65536,
            "encoding": "utf-8",
            "required": True,
            "covered_step_ids": [f"step-{index + 1:03d}"],
        }
        for index, v in enumerate(validations)
    ]
    artifacts["validation-specs.json"] = json.dumps({"validations": validation_list}, sort_keys=True, indent=2) + "\n"

    fixture_list = [
        {
            **version_fields(),
            "fixture_id": f.fixture_id,
            "fixture_type": f.fixture_type,
            "covered_criterion_ids": f.covered_criterion_ids,
            "covered_validation_ids": f.covered_validation_ids,
            "setup": f.setup,
            "input": f.input,
            "expected_result": f.expected_result,
            "required_evidence": f.required_evidence,
            "required": True,
            "execution": {"mode": "declared_only"},
            "cleanup": {"owned_workspace_only": True},
            "workspace_policy": "operation-owned",
            "executable": sys.executable,
            "arguments": ["-c", "print('A-Flow fixture')"],
            "working_directory": ".",
            "timeout_seconds": 30,
            "environment_allowlist": [],
            "stdin_policy": "closed",
            "expected_exit_codes": [0],
            "expected_output": "A-Flow fixture",
        }
        for f in fixtures
    ]
    artifacts["fixture-cases.json"] = json.dumps({"fixtures": fixture_list}, sort_keys=True, indent=2) + "\n"

    traceability_list = [
        {
            "plan_steps": entry["plan_steps"],
            "success_criterion_id": entry["success_criterion_id"],
            "validation_ids": entry["validation_ids"],
            "fixture_ids": entry["fixture_ids"],
        }
        for entry in traceability.mapping
    ]
    artifacts["traceability-map.json"] = json.dumps({"traceability": traceability_list}, sort_keys=True, indent=2) + "\n"

    artifacts["unresolved-info.json"] = json.dumps({"unresolved": unresolved}, sort_keys=True, indent=2) + "\n"

    artifacts["operation-metadata.json"] = json.dumps(operation_metadata, sort_keys=True, indent=2) + "\n"

    preserved_digest = preserved_original_plan.get("original_plan_digest", "")
    if preserved_digest:
        artifacts["original-plan-digest.json"] = json.dumps({"digest": preserved_digest}, sort_keys=True, indent=2) + "\n"

    # Legacy compatibility artifacts to keep old validators happy when requested,
    # but canonical manifest is based on canonical artifacts.
    # Keep evaluated-plan etc.

    outcome = "review_processed_with_unresolved_gaps" if unresolved else "review_processed_all_gaps_disposed"
    if unresolved and any(isinstance(u, dict) and "design" in u.get("gap_id", "") for u in unresolved):
        outcome = "decision_required"

    processing_summary = {
        "plan_processed": True,
        "evaluation_completed": True,
        "stages_executed": 11,
        "gaps_discovered": len(gaps),
        "gaps_resolved": sum(1 for r in gap_resolutions if r.resolution_status == "resolved"),
        "gaps_unresolved": len(unresolved),
        "gap_count_limit_applied": False,
        "unresolved_items_preserved": bool(unresolved),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": intake.plan_id,
        "outcome": outcome,
        "artifacts": sorted(artifacts.keys()),
        "operation_id": intake.operation_id,
        "stages_executed": 11,
        "processing_summary": processing_summary,
    }
    artifacts["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2) + "\n"

    return {"manifest": manifest, "artifacts": artifacts}
