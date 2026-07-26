from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .config import load_config
from .core import ReviewError, review_plan, validate_bundle, write_bundle
from .http_service import serve
from .local_setup import DEFAULT_MODEL, SetupError, _json_request, setup_ollama
from .semantic import local_semantic_notes
from .work_state import load_continuation_package, resume_operation, update_work_state
from .verification import status_operation, verify_operation

def _print(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))

def _normalize_operation_id(operation_id, plan_id):
    if operation_id and operation_id.strip():
        return operation_id.strip()
    return f"aflow-{plan_id}"

def _review(args):
    source = Path(args.plan)
    original_bytes = source.read_bytes()
    original = original_bytes.decode("utf-8", errors="replace")
    repo_root = args.repository_root if hasattr(args, "repository_root") and args.repository_root else str(Path.cwd())
    operation_id = _normalize_operation_id(getattr(args, "operation_id", None), args.plan_id)
    
    # Call semantic with operation context for provenance
    notes = []
    if not args.no_semantic:
        from .stages.operation_setup import setup_operation
        operation = setup_operation(operation_id=operation_id, plan_id=args.plan_id, repository_root=repo_root)
        notes, _ = local_semantic_notes(original, operation_id=operation_id, operation_dir_path=operation.operation_dir)
    
    prompt = f"CLI review of {args.plan} for plan_id={args.plan_id}"
    bundle = review_plan(original_plan=original, plan_id=args.plan_id, operation_id=operation_id, repository_root=repo_root, user_prompt=prompt, repository_context={"cli_plan_path": str(source)}, semantic_notes=notes, force_canonical=True, original_bytes=original_bytes)
    result = write_bundle(bundle, Path(args.output_dir))
    gap_data = json.loads(bundle["artifacts"].get("gap-records.json", '{"gaps":[]}'))
    resolution_data = json.loads(bundle["artifacts"].get("gap-resolution-history.json", "[]"))
    criteria_data = json.loads(bundle["artifacts"].get("success-criteria.json", '{"criteria":[]}'))
    validation_data = json.loads(bundle["artifacts"].get("validation-specs.json", '{"validations":[]}'))
    fixture_data = json.loads(bundle["artifacts"].get("fixture-cases.json", '{"fixtures":[]}'))
    # Derive summary fields from actual state, never hard-coded values.
    stages_executed = bundle["manifest"].get("stages_executed", bundle["manifest"].get("processing_summary", {}).get("stages_executed"))
    digest_data = json.loads(bundle["artifacts"].get("original-plan-digest.json", "{}"))
    plan_preserved = digest_data.get("digest") == hashlib.sha256(original_bytes).hexdigest()
    try:
        next_exact_action = load_continuation_package(operation_id).get("next_exact_action")
    except (ReviewError, OSError, KeyError):
        next_exact_action = None
    _print({"result": result, "manifest": bundle["manifest"], "operation_id": operation_id, "stages_executed": stages_executed, "summary": {"plan_preserved": plan_preserved, "gaps_discovered": len(gap_data.get("gaps", [])), "gaps_resolved": sum(1 for x in resolution_data if x.get("resolution_status") == "resolved"), "gaps_unresolved": sum(1 for x in resolution_data if x.get("resolution_status") == "unresolved"), "success_criteria_prepared": len(criteria_data.get("criteria", [])), "validations_prepared": len(validation_data.get("validations", [])), "fixtures_prepared": len(fixture_data.get("fixtures", [])), "next_exact_action": next_exact_action}})
    return 0

def _validate(args):
    root = Path(args.output_dir)
    artifacts = {path.name: path.read_text(encoding="utf-8") for path in root.iterdir() if path.is_file()}
    manifest = json.loads(artifacts["manifest.json"])
    _print(validate_bundle({"manifest": manifest, "artifacts": artifacts}))
    return 0

def _setup(args):
    if not args.yes:
        raise SetupError("Run aflow setup --yes to approve")
    result = setup_ollama(model=args.model)
    _print({"endpoint": result.endpoint, "model": result.model, "state": "ready"})
    return 0

def _doctor():
    config = load_config()
    endpoint = str(config.get("base_url", "http://127.0.0.1:11434"))
    tags = _json_request("/api/tags") if endpoint == "http://127.0.0.1:11434" else None
    model = config.get("model")
    names = {item.get("name") for item in (tags or {}).get("models", []) if isinstance(item, dict)}
    _print({"provider": config.get("provider"), "endpoint": endpoint, "model": model, "ready": bool(model and model in names)})
    return 0 if model and model in names else 1

def _demo(args):
    plan = "# Demo plan\nObjective: immutable plan review.\nSuccess criteria: source plan unchanged.\nValidation: validate bundle.\nFixture: demo plan.\n"
    operation_id = _normalize_operation_id(getattr(args, "operation_id", None), "demo-plan")
    repo_root = args.repository_root if hasattr(args, "repository_root") and args.repository_root else str(Path.cwd())
    bundle = review_plan(original_plan=plan, plan_id="demo-plan", operation_id=operation_id, repository_root=repo_root, user_prompt="Demo plan review", force_canonical=True)
    result = write_bundle(bundle, Path(args.output_dir))
    _print({"result": result, "manifest": bundle["manifest"], "operation_id": operation_id, "stages_executed": 11})
    return 0

def _serve(args):
    serve(host=args.host, port=args.port)
    return 0

def _progress(args):
    data = json.loads(Path(args.update_file).read_text(encoding="utf-8"))
    result = update_work_state(operation_id=args.operation_id, expected_revision=data["expected_revision"], completed_step_ids=data.get("completed_step_ids", []), current_step_id=data.get("current_step_id"), changed_files=data.get("changed_files", []), created_files=data.get("created_files", []), deleted_files=data.get("deleted_files", []), validation_records=data.get("validation_records", []), unresolved_items=data.get("unresolved_items", []), last_completed_action=data.get("last_completed_action"), next_exact_action=data.get("next_exact_action"))
    _print(result)
    return 0

def _resume(args):
    result = resume_operation(operation_id=args.operation_id)
    result["summary"] = {
        "validated_completed_work": result.get("validated_results", []) or result.get("completed_work", []),
        "current_work": result.get("current_work"),
        "remaining_work": result.get("remaining_work", []),
        "failed_or_stale_evidence": result.get("failed_results", []) + result.get("stale_results", []),
        "unresolved_items": result.get("unresolved_items", []),
        "next_exact_action": result.get("next_exact_action"),
    }
    _print(result)
    return 0

def _verify(args):
    result = verify_operation(args.operation_id)
    result["summary"] = {
        "executed": len(result.get("validation_results", [])) + len(result.get("fixture_results", [])),
        "passed": len(result.get("passed_validation_ids", [])) + len(result.get("passed_fixture_ids", [])),
        "failed": len(result.get("failed_validation_ids", [])) + len(result.get("failed_fixture_ids", [])),
        "timed_out": len(result.get("timed_out_validation_ids", [])) + len(result.get("timed_out_fixture_ids", [])),
        "unsupported": len(result.get("unsupported_validation_ids", [])) + len(result.get("unsupported_fixture_ids", [])),
        "stale": len(result.get("stale_criterion_ids", [])),
        "criteria_satisfied": len(result.get("satisfied_criterion_ids", [])),
        "criteria_not_satisfied": len(result.get("failed_criterion_ids", [])),
        "criteria_not_verified": len(result.get("unverified_criterion_ids", [])),
        "operation_status": result.get("verification_status"),
        "next_exact_action": result.get("next_exact_action"),
    }
    _print(result)
    return 0 if result.get("verification_status") == "passed" else 1

def _status(args):
    _print(status_operation(args.operation_id))
    return 0

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="aflow")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("review")
    review.add_argument("--plan", required=True)
    review.add_argument("--plan-id", required=True)
    review.add_argument("--output-dir", required=True)
    review.add_argument("--operation-id", required=False, default=None)
    review.add_argument("--repository-root", required=False, default=None)
    review.add_argument("--no-semantic", action="store_true")
    validate = commands.add_parser("validate-bundle")
    validate.add_argument("--output-dir", required=True)
    setup = commands.add_parser("setup")
    setup.add_argument("--yes", action="store_true")
    setup.add_argument("--model", default=DEFAULT_MODEL)
    commands.add_parser("doctor")
    demo = commands.add_parser("demo")
    demo.add_argument("--output-dir", required=True)
    demo.add_argument("--operation-id", required=False, default=None)
    demo.add_argument("--repository-root", required=False, default=None)
    serve_command = commands.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    commands.add_parser("mcp")
    progress = commands.add_parser("progress")
    progress.add_argument("--operation-id", required=True)
    progress.add_argument("--update-file", required=True)
    resume_cmd = commands.add_parser("resume")
    resume_cmd.add_argument("--operation-id", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--operation-id", required=True)
    status = commands.add_parser("status")
    status.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "review": return _review(args)
        if args.command == "validate-bundle": return _validate(args)
        if args.command == "setup": return _setup(args)
        if args.command == "doctor": return _doctor()
        if args.command == "demo": return _demo(args)
        if args.command == "serve": return _serve(args)
        if args.command == "progress": return _progress(args)
        if args.command == "resume": return _resume(args)
        if args.command == "verify": return _verify(args)
        if args.command == "status": return _status(args)
        from .mcp_server import run_stdio; run_stdio(); return 0
    except (OSError, ReviewError, SetupError, ValueError, KeyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

