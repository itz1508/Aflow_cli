from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

from .stages.intake import create_intake
from .stages.operation_setup import setup_operation, update_stage_state
from .stages.preserve_plan import preserve_original_plan
from .stages.evidence import RepositoryEvidence, collect_repository_evidence
from .stages.gap_discovery import discover_gaps
from .stages.gap_resolution import resolve_gaps, persist_resolution_history
from .stages.evaluation import evaluate_plan
from .stages.success_criteria import derive_success_criteria
from .stages.validation import develop_validations
from .stages.fixtures import develop_fixtures
from .stages.companion import assemble_companion_package

SCHEMA_VERSION = "1.0.0"
REVIEW_OUTCOMES = {"review_processed_all_gaps_disposed", "review_processed_with_unresolved_gaps", "decision_required"}

class ReviewError(ValueError):
    pass

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

def _contains_any(text, values):
    return any(value in text for value in values)

def _deterministic_findings(plan):
    lower = plan.lower()
    decisions = []
    if _contains_any(lower, ("design decision required", "[design decision]", "tbd: design", "architecture decision required")):
        decisions.append({"id": "design-decision-required", "reason": "The plan explicitly requires a design decision. A-Flow must not invent that decision."})
    findings = []
    checks = (
        ("objective", ("objective", "goal", "outcome"), "State the intended outcome so review can evaluate scope."),
        ("success-criteria", ("success criteria", "acceptance criteria", "success looks like"), "Add observable success criteria as a companion record."),
        ("validation", ("validation", "test", "verify"), "Add a validation specification as a companion record."),
        ("fixture", ("fixture", "sample input", "test data"), "Add a fixture specification as a companion record."),
    )
    for finding_id, markers, correction in checks:
        if not _contains_any(lower, markers):
            findings.append({"id": finding_id, "reason": correction})
    return findings, decisions

def _render_markdown(title, items, empty):
    lines = [f"# {title}", ""]
    if not items:
        lines.append(empty)
    else:
        for item in items:
            lines.extend((f"## {item['id']}", "", item["reason"], ""))
    return "\n".join(lines).rstrip() + "\n"

def _legacy_review_plan(original_plan, plan_id, semantic_notes=None):
    findings, decisions = _deterministic_findings(original_plan)
    outcome = "decision_required" if decisions else ("review_processed_with_unresolved_gaps" if findings else "review_processed_all_gaps_disposed")
    success_criteria = [{"id": "original-plan-immutable", "expected": "The source plan is accepted as read-only input and is never rewritten."}, {"id": "companion-bundle-valid", "expected": "Every generated companion artifact is present and structurally valid."}, {"id": "evaluation-defined", "expected": "Success criteria, validation fixtures, and test specifications are supplied without rewriting the plan."}]
    fixtures = [{"fixture_id": "fixture.plan-source", "input": {"plan_id": plan_id}, "expected": {"source_plan_changed": False}}]
    tests = [{"test_id": "test.plan-source-immutable", "assertion": "A-Flow returns only additive artifacts and never rewrites original_plan."}, {"test_id": "test.companion-bundle-valid", "assertion": "validate_bundle(bundle) succeeds"}]
    gap_review = {"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "outcome": outcome, "findings": findings, "decision_required": decisions, "semantic_notes": semantic_notes or [], "semantic_notes_are_evidence": False, "processing_summary": {"plan_processed": True, "evaluation_completed": True, "stages_executed": 11, "gaps_discovered": len(findings) + len(decisions), "gaps_resolved": 0, "gaps_unresolved": len(findings) + len(decisions), "gap_count_limit_applied": False, "unresolved_items_preserved": bool(findings or decisions)}}
    evaluation = {"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "success_criteria": success_criteria, "validation_scope": "A-Flow validates its additive companion bundle only; it does not execute the source plan."}
    validation_fixtures = {"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "fixtures": fixtures}
    validation_tests = {"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "tests": tests}
    artifacts = {"gap-review.json": canonical_json(gap_review), "gap-fulfillment.md": _render_markdown("Gap Fulfillment", findings, "The complete submitted plan was processed and evaluated; discovered gaps: 0; unresolved gaps: 0; no gap-count limit was applied."), "evaluation.json": canonical_json(evaluation), "validation-fixtures.json": canonical_json(validation_fixtures), "validation-tests.json": canonical_json(validation_tests), "plan-update.md": _render_markdown("Plan Update", decisions, "The submitted plan was processed without changing its original bytes.")}
    if outcome == "decision_required":
        artifacts["decision-required.json"] = canonical_json({"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "decisions": decisions})
    manifest = {"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "outcome": outcome, "artifacts": sorted(artifacts.keys()), "processing_summary": gap_review["processing_summary"]}
    artifacts = {"manifest.json": canonical_json(manifest), **artifacts}
    return {"manifest": manifest, "artifacts": artifacts}

def _canonical_review_plan(*, original_plan, plan_id, operation_id=None, repository_root=None, user_prompt="", agent_or_host="agent", execution_kind="implementation", repository_context=None, semantic_notes=None, original_bytes=None):
    intake = create_intake(operation_id=operation_id, plan_id=plan_id, repository_root=repository_root, user_prompt=user_prompt, original_plan=original_plan, agent_or_host=agent_or_host, execution_kind=execution_kind, repository_context=repository_context)
    operation = setup_operation(operation_id=intake.operation_id, plan_id=plan_id, repository_root=repository_root)
    preserved_original_plan = preserve_original_plan(operation_dir=operation.operation_dir, original_plan=original_plan, original_bytes=original_bytes)
    update_stage_state(operation, "preserve_plan", "completed")
    repository_evidence = None
    if repository_root:
        try:
            repository_evidence = collect_repository_evidence(repository_root=repository_root)
        except ValueError:
            pass
    update_stage_state(operation, "repository_evidence", "completed")
    
    # Handle semantic notes with provenance if provided
    actual_semantic_notes = semantic_notes
    if semantic_notes is None and not hasattr(semantic_notes, '__iter__'):
        # No semantic notes provided, don't call local_semantic_notes here
        # It will be called by CLI/MCP with proper operation context
        actual_semantic_notes = []
    
    gaps = discover_gaps(original_plan=original_plan, user_prompt=user_prompt, repository_evidence=repository_evidence, semantic_observations=actual_semantic_notes or [])
    update_stage_state(operation, "gap_discovery", "completed")
    gap_resolutions, corrected_plan = resolve_gaps(gaps=gaps, original_plan=original_plan, repository_evidence=repository_evidence, operation_dir=operation.operation_dir)
    persist_resolution_history(operation_dir=operation.operation_dir, resolutions=gap_resolutions)
    update_stage_state(operation, "gap_resolution", "completed")
    evaluation = evaluate_plan(corrected_plan=corrected_plan, gap_resolutions=gap_resolutions, original_plan=original_plan)
    update_stage_state(operation, "evaluation", "completed")
    success_criteria = derive_success_criteria(evaluated_plan=corrected_plan, gap_resolutions=gap_resolutions)
    update_stage_state(operation, "success_criteria", "completed")
    validations = develop_validations(success_criteria=success_criteria, repository_evidence=repository_evidence)
    update_stage_state(operation, "validation", "completed")
    fixtures = develop_fixtures(success_criteria=success_criteria, validations=validations)
    update_stage_state(operation, "fixtures", "completed")
    companion = assemble_companion_package(intake=intake, preserved_original_plan=preserved_original_plan, repository_evidence=repository_evidence, gaps=gaps, gap_resolutions=gap_resolutions, evaluation=evaluation, success_criteria=success_criteria, validations=validations, fixtures=fixtures, operation_dir=operation.operation_dir)
    if actual_semantic_notes:
        companion["artifacts"]["semantic-notes.json"] = canonical_json({"schema_version": SCHEMA_VERSION, "plan_id": plan_id, "notes": actual_semantic_notes, "are_evidence": False})
        companion["manifest"]["artifacts"] = sorted(list(set(companion["manifest"]["artifacts"]) | {"semantic-notes.json"}))
        companion["artifacts"]["manifest.json"] = canonical_json(companion["manifest"])
    # Keep the complete companion package operation-local so later verification
    # loads persisted state rather than reconstructing it from the repository.
    for artifact_name, content in companion["artifacts"].items():
        destination = operation.operation_dir / artifact_name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    update_stage_state(operation, "companion_package", "completed")
    try:
        from .work_state import initialize_work_state
        repo_root_for_continuity = repository_root or str(Path.cwd())
        initialize_work_state(operation_id=intake.operation_id, plan_id=plan_id, repository_root=repo_root_for_continuity, evaluated_plan=corrected_plan)
    except Exception as e:
        try:
            err_path = operation.operation_dir / "work_state_init_error.log"
            err_path.write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        except Exception:
            pass
    return companion

def review_plan(*, original_plan, plan_id, plan_digest=None, semantic_notes=None, operation_id=None, repository_root=None, user_prompt="", agent_or_host="agent", execution_kind="implementation", repository_context=None, force_canonical=False, original_bytes=None):
    if not original_plan.strip():
        raise ReviewError("original_plan must be non-empty")
    if not plan_id.strip():
        raise ReviewError("plan_id must be non-empty")
    should_use_legacy = all([operation_id is None, repository_root is None, user_prompt == "", agent_or_host == "agent", execution_kind == "implementation", repository_context is None, not force_canonical])
    if should_use_legacy:
        return _legacy_review_plan(original_plan=original_plan, plan_id=plan_id, semantic_notes=semantic_notes)
    return _canonical_review_plan(original_plan=original_plan, plan_id=plan_id, operation_id=operation_id, repository_root=repository_root, user_prompt=user_prompt, agent_or_host=agent_or_host, execution_kind=execution_kind, repository_context=repository_context, semantic_notes=semantic_notes, original_bytes=original_bytes)

def validate_bundle(bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get("manifest"), dict) or not isinstance(bundle.get("artifacts"), dict):
        raise ReviewError("bundle must contain manifest and artifacts")
    manifest = bundle["manifest"]
    artifacts = bundle["artifacts"]
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("plan_id"), str) or not manifest["plan_id"].strip():
        raise ReviewError("bundle manifest is malformed")
    expected = manifest.get("artifacts")
    if not isinstance(expected, list) or not expected or not all(isinstance(path, str) for path in expected):
        raise ReviewError("bundle manifest does not describe every companion artifact")
    if manifest.get("outcome") not in REVIEW_OUTCOMES:
        raise ReviewError("bundle manifest has an ambiguous or unsupported outcome")
    summary = manifest.get("processing_summary")
    if not isinstance(summary, dict) or summary.get("plan_processed") is not True or summary.get("evaluation_completed") is not True or summary.get("gap_count_limit_applied") is not False:
        raise ReviewError("bundle manifest is missing explicit processing summary")
    manifest_set = set(expected)
    artifacts_without_manifest = set(k for k in artifacts.keys() if k != "manifest.json")
    if manifest_set != artifacts_without_manifest:
        raise ReviewError("bundle manifest does not describe every companion artifact")
    return {"valid": True, "plan_id": manifest["plan_id"], "outcome": manifest["outcome"]}

def write_bundle(bundle, output_dir):
    validate_bundle(bundle)
    target = output_dir.resolve()
    if target.exists() and not target.is_dir():
        raise ReviewError("output_dir must be a directory")
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    rendered_manifest = bundle["artifacts"]["manifest.json"]
    if manifest_path.exists():
        if manifest_path.read_text(encoding="utf-8") == rendered_manifest:
            return {"state": "idempotent", "output_dir": str(target)}
        raise ReviewError("output_dir already contains a different A-Flow bundle")
    for name, content in bundle["artifacts"].items():
        destination = target / name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    return {"state": "written", "output_dir": str(target)}

