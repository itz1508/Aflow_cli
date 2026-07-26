"""Independent execution of persisted validations and fixtures."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import ContractError, execute_command, version_fields, validate_versioned
from .stages.operation_setup import _get_operations_root
from .work_state import ReviewError, _load_json, _operation_dir, update_work_state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    os.replace(tmp, path)


def _load_operation(op_dir: Path) -> tuple[dict, dict, dict, dict, dict, dict]:
    config = _load_json(op_dir / "operation-config.json")
    if not config.get("operation_id"):
        raise ReviewError("operation configuration missing operation_id")
    updated = _load_json(op_dir / "updated-plan.json")
    work = _load_json(op_dir / "work-status.json")
    validation_report = _load_json(op_dir / "validation-report.json")
    for obj in (updated, work, validation_report):
        if obj.get("operation_id") != config["operation_id"]:
            raise ReviewError("operation artifact ID mismatch")
    criteria = _load_json(op_dir / "success-criteria.json")
    specs = _load_json(op_dir / "validation-specs.json")
    fixtures = _load_json(op_dir / "fixture-cases.json")
    return config, updated, work, validation_report, criteria, {"validations": specs.get("validations", []), "fixtures": fixtures.get("fixtures", [])}


def _criterion_status(criterion: dict, results: list[dict]) -> str:
    covered = [r for r in results if criterion["criterion_id"] in r.get("covered_criterion_ids", []) and r.get("required", True)]
    if any(r.get("status") == "stale" for r in covered):
        return "stale"
    if any(r.get("status") == "failed" for r in covered):
        return "not_satisfied"
    if covered and all(r.get("status") == "passed" for r in covered):
        return "satisfied"
    return "not_verified"


def verify_operation(operation_id: str, expected_revision: int | None = None) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    if not op_dir.exists():
        raise ReviewError(f"unknown operation_id: {operation_id}")
    config, updated, work, _, criteria_doc, specs_doc = _load_operation(op_dir)
    repo_root = Path(config.get("repository_root") or Path.cwd()).resolve()
    attempt_root = op_dir / "verification" / "attempts"
    attempt_root.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.name) for p in attempt_root.iterdir() if p.is_dir() and p.name.isdigit()]
    attempt_no = max(numbers, default=0) + 1
    root = attempt_root / f"{attempt_no:06d}"
    evidence_dir = root / "evidence"
    root.mkdir(parents=True)
    started = _now()
    validation_results: list[dict] = []
    for spec in specs_doc["validations"]:
        try:
            validate_versioned(spec, "validation")
            result = execute_command(spec, repo_root, evidence_dir)
        except (ContractError, KeyError, TypeError) as exc:
            result = {"validation_id": spec.get("validation_id"), "status": "not_run", "error": str(exc)}
        result["covered_criterion_ids"] = spec.get("covered_criterion_ids", [])
        result["covered_step_ids"] = spec.get("covered_step_ids", [])
        result["required"] = bool(spec.get("required", True))
        validation_results.append(result)
    fixture_results = []
    for fixture in specs_doc["fixtures"]:
        try:
            validate_versioned(fixture, "fixture")
            if not all(k in fixture for k in ("fixture_id", "covered_criterion_ids", "covered_validation_ids", "required", "executable", "arguments", "working_directory", "timeout_seconds", "expected_exit_codes")):
                raise ContractError("fixture missing required fields")
            command_result = execute_command({**fixture, "validation_id": fixture["fixture_id"]}, repo_root, evidence_dir)
            if command_result.get("status") == "passed" and fixture.get("expected_output") and fixture["expected_output"] not in command_result.get("stdout", ""):
                command_result["status"] = "failed"
                command_result["error"] = "expected output was not observed"
            fixture_results.append({**command_result, "fixture_id": fixture["fixture_id"], "covered_criterion_ids": fixture["covered_criterion_ids"], "required": fixture["required"]})
        except (ContractError, KeyError, TypeError) as exc:
            fixture_results.append({"fixture_id": fixture.get("fixture_id"), "status": "not_run", "error": str(exc), "covered_criterion_ids": fixture.get("covered_criterion_ids", []), "required": bool(fixture.get("required", True))})
    all_results = validation_results + fixture_results
    assessments = []
    for criterion in criteria_doc.get("criteria", []):
        status = _criterion_status(criterion, all_results)
        assessments.append({"criterion_id": criterion["criterion_id"], "related_plan_step_ids": criterion.get("source_plan_step_ids", []), "validation_ids": [r["validation_id"] for r in validation_results if criterion["criterion_id"] in r.get("covered_criterion_ids", [])], "fixture_ids": [r["fixture_id"] for r in fixture_results if criterion["criterion_id"] in r.get("covered_criterion_ids", [])], "status": status, "evidence_paths": [p for r in all_results for p in r.get("evidence_paths", [])], "explanation": f"Current evidence classified criterion as {status}."})
    status = "passed" if all(a["status"] == "satisfied" for a in assessments) and all(r["status"] == "passed" for r in all_results if r.get("required", True)) else "incomplete"
    state_records = []
    completed_step_ids = []
    for item in validation_results:
        if item.get("status") == "passed" and item.get("covered_step_ids"):
            completed_step_ids.extend(item["covered_step_ids"])
            state_records.append({"validation_id": item["validation_id"], "covered_step_ids": item["covered_step_ids"], "covered_file_paths": [], "method": "command", "command": " ".join(item.get("command", [])), "exit_code": item.get("exit_code"), "status": "passed", "captured_output_path": item.get("evidence_paths", [""])[0], "evidence_paths": item.get("evidence_paths", []), "file_states": {}, "recorded_at": _now(), "last_verified_at": _now()})
    updated_state = None
    if completed_step_ids:
        updated_state = update_work_state(operation_id=operation_id, expected_revision=updated.get("work_state_revision"), completed_step_ids=sorted(set(completed_step_ids)), current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=state_records, unresolved_items=[], last_completed_action="Independent verification executed persisted validations", next_exact_action=None)
    result = {**version_fields(), "operation_id": operation_id, "work_state_revision": updated.get("work_state_revision"), "verification_attempt": attempt_no, "verification_status": status, "satisfied_criterion_ids": [a["criterion_id"] for a in assessments if a["status"] == "satisfied"], "failed_criterion_ids": [a["criterion_id"] for a in assessments if a["status"] == "not_satisfied"], "unverified_criterion_ids": [a["criterion_id"] for a in assessments if a["status"] == "not_verified"], "stale_criterion_ids": [a["criterion_id"] for a in assessments if a["status"] == "stale"], "passed_validation_ids": [r["validation_id"] for r in validation_results if r["status"] == "passed"], "failed_validation_ids": [r["validation_id"] for r in validation_results if r["status"] == "failed"], "timed_out_validation_ids": [r["validation_id"] for r in validation_results if r["status"] == "timed_out"], "unsupported_validation_ids": [r["validation_id"] for r in validation_results if r["status"] == "unsupported"], "passed_fixture_ids": [r["fixture_id"] for r in fixture_results if r["status"] == "passed"], "failed_fixture_ids": [r["fixture_id"] for r in fixture_results if r["status"] == "failed"], "timed_out_fixture_ids": [], "unsupported_fixture_ids": [], "changed_files": work.get("changed_files", []), "evidence_paths": [str(root)], "remaining_work": work.get("remaining_step_ids", []), "unresolved_items": work.get("unresolved_items", []), "next_exact_action": None if status == "passed" else "Repair or define affected required evidence and rerun verification", "started_at": started, "completed_at": _now(), "criteria_assessments": assessments, "validation_results": validation_results, "fixture_results": fixture_results}
    if updated_state:
        result["work_state_revision"] = updated_state["work_state_revision"]
        result["remaining_work"] = [item.get("step_id") for item in updated_state.get("remaining_work", [])]
    _atomic_json(root / "attempt.json", {**version_fields(), "operation_id": operation_id, "attempt": attempt_no, "started_at": started, "completed_at": result["completed_at"]})
    _atomic_json(root / "validations.json", {**version_fields(), "operation_id": operation_id, "results": validation_results})
    _atomic_json(root / "fixtures.json", {**version_fields(), "operation_id": operation_id, "results": fixture_results})
    _atomic_json(root / "criteria.json", {**version_fields(), "operation_id": operation_id, "assessments": assessments})
    _atomic_json(root / "final-result.json", result)
    _atomic_json(op_dir / "final-result.json", result)
    _atomic_json(op_dir / "verification-current.json", {**version_fields(), "operation_id": operation_id, "attempt": attempt_no, "path": str(root / "final-result.json")})
    return result


def status_operation(operation_id: str) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    _, updated, work, report, _, _ = _load_operation(op_dir)
    latest = _load_json(op_dir / "final-result.json") if (op_dir / "final-result.json").exists() else None
    return {"operation_id": operation_id, "operation_status": work.get("operation_status"), "work_state_revision": work.get("work_state_revision"), "current_step": work.get("current_step_id"), "completed_count": len(work.get("completed_step_ids", [])), "remaining_count": len(work.get("remaining_step_ids", [])), "unresolved_count": len(work.get("unresolved_items", [])), "failed_validation_count": sum(1 for x in report.get("validations", []) if x.get("status") == "failed"), "stale_validation_count": sum(1 for x in report.get("validations", []) if x.get("status") == "stale"), "latest_verification_status": latest.get("verification_status") if latest else None, "next_exact_action": work.get("next_exact_action")}
