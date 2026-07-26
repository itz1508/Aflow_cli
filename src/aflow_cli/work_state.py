"""Work-state continuity."""
from __future__ import annotations
import datetime, json, os, time
from pathlib import Path
from typing import Any
class ReviewError(ValueError): pass
from .stages.operation_setup import _get_operations_root

STEP_STATUSES = {"pending", "in_progress", "completed_unverified", "completed", "invalidated"}
OPERATION_STATUSES = {"not_started", "in_progress", "completed", "invalidated"}
VALIDATION_STATUSES = {"not_run", "passed", "failed", "stale"}
ALLOWED_TRANSITIONS = {
    "pending": {"in_progress"},
    "in_progress": {"completed_unverified", "completed", "invalidated"},
    "completed_unverified": {"completed", "in_progress"},
    "completed": {"in_progress", "invalidated"},
    "invalidated": {"in_progress"},
}
WORK_STATE_FILES = {
    "updated_plan": "updated-plan.json",
    "work_status": "work-status.json",
    "validation_report": "validation-report.json",
    "continuation_package": "continuation-package.json",
}
LOCK_FILE = ".lock"

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _file_state_for_path(repo_root: Path, rel_path: str) -> dict[str, Any]:
    target = repo_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
    if not target.exists() or not target.is_file():
        return {"exists": False, "size_bytes": 0, "modified_time": _now_iso()}
    stat = target.stat()
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc).isoformat()
    return {"exists": True, "size_bytes": stat.st_size, "modified_time": mtime}

def _operation_dir(operation_id: str) -> Path:
    if not operation_id or not operation_id.strip():
        raise ReviewError("operation_id must be non-empty")
    return _get_operations_root() / operation_id

def _acquire_lock(op_dir: Path, timeout: float = 5.0):
    lock_path = op_dir / LOCK_FILE
    op_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock_path
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 10:
                    lock_path.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.time() - start > timeout:
                raise ReviewError(f"operation locked: {op_dir.name}")
            time.sleep(0.05)

def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

def _check_no_conversation(obj: Any) -> None:
    txt = json.dumps(obj, ensure_ascii=False).lower()
    for term in ["user message", "assistant message", "chat history", "conversation history", "interaction log"]:
        if term in txt:
            raise ReviewError(f"conversation leak: {term}")

def _derive_steps(evaluated_plan: str) -> list[dict[str, Any]]:
    lines = evaluated_plan.split("\n")
    steps: list[dict[str, Any]] = []
    idx = 0
    for line in lines:
        s = line.strip()
        if s.startswith("## ") and len(s) > 3:
            title = s[3:].strip()
            low = title.lower()
            if not title or low.startswith("finding") or low.startswith("decision"):
                continue
            idx += 1
            steps.append({
                "step_id": f"step-{idx:03d}",
                "title": title[:120],
                "description": title,
                "dependency_step_ids": [f"step-{i:03d}" for i in range(1, idx)],
                "status": "pending",
                "related_file_paths": [],
                "validation_ids": [],
            })
    if not steps:
        paras = [p.strip() for p in evaluated_plan.split("\n\n") if p.strip()]
        for i, para in enumerate(paras[:10], 1):
            t = para.split("\n")[0][:100] or f"Step {i}"
            steps.append({
                "step_id": f"step-{i:03d}",
                "title": t,
                "description": para[:500],
                "dependency_step_ids": [f"step-{j:03d}" for j in range(1, i)],
                "status": "pending",
                "related_file_paths": [],
                "validation_ids": [f"validation-{i:03d}"],
            })
    if not steps:
        steps.append({
            "step_id": "step-001",
            "title": "Complete implementation",
            "description": evaluated_plan[:500],
            "dependency_step_ids": [],
            "status": "pending",
            "related_file_paths": [],
            "validation_ids": ["validation-001"],
        })
    for s in steps:
        if not s["validation_ids"]:
            s["validation_ids"] = [f"validation-{s['step_id'].split('-')[1]}"]
    return steps

def _validate_step(step: dict[str, Any]) -> None:
    if not isinstance(step.get("step_id"), str) or not step["step_id"].strip():
        raise ReviewError("step_id must be non-empty")
    if step.get("status") not in STEP_STATUSES:
        raise ReviewError(f"invalid step status {step.get('status')}")
    if not isinstance(step.get("dependency_step_ids"), list):
        raise ReviewError("dependency_step_ids must be list")
    if not isinstance(step.get("related_file_paths"), list):
        raise ReviewError("related_file_paths must be list")

def _validate_validation_rec(rec: dict[str, Any]) -> None:
    for k in ["validation_id", "covered_step_ids", "method", "status", "recorded_at"]:
        if k not in rec:
            raise ReviewError(f"validation missing {k}")
    if rec["status"] not in VALIDATION_STATUSES:
        raise ReviewError(f"invalid validation status {rec['status']}")
    if not isinstance(rec["covered_step_ids"], list) or not rec["covered_step_ids"]:
        raise ReviewError("covered_step_ids must be non-empty")
    has_cmd = rec.get("command") and isinstance(rec["command"], str) and rec["command"].strip()
    has_ins = rec.get("inspection_method") and isinstance(rec["inspection_method"], str) and rec["inspection_method"].strip()
    if not has_cmd and not has_ins:
        raise ReviewError("validation requires command or inspection_method")
    if rec.get("method") == "command" and not has_cmd:
        raise ReviewError("command method requires command")
    if rec.get("captured_output_path") is None and not rec.get("evidence_paths"):
        if not rec.get("file_states"):
            raise ReviewError("validation requires output path or evidence or file_states")
    if not isinstance(rec.get("file_states", {}), dict):
        raise ReviewError("file_states must be dict")

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReviewError(f"missing artifact: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ReviewError(f"malformed JSON {path.name}: {e}")
    if not isinstance(data, dict):
        raise ReviewError(f"{path.name} must be JSON object")
    return data

def _build_continuation_pkg(*, operation_id: str, repository_root: str, updated_plan: dict, work_status: dict, validation_report: dict, op_dir: Path) -> dict[str, Any]:
    rev = updated_plan.get("work_state_revision") or work_status.get("work_state_revision") or 1
    completed_ids = set(work_status.get("completed_step_ids", []))
    current_id = work_status.get("current_step_id")
    steps_by_id = {s["step_id"]: s for s in updated_plan.get("steps", [])}
    completed_work = []
    for sid in completed_ids:
        s = steps_by_id.get(sid)
        if s:
            completed_work.append({"step_id": sid, "title": s.get("title"), "status": s.get("status")})
    current_work = steps_by_id.get(current_id, {}) if current_id else {}
    remaining = []
    for sid in work_status.get("remaining_step_ids", []):
        if sid not in completed_ids:
            s = steps_by_id.get(sid)
            if s:
                remaining.append(s)
    validations = validation_report.get("validations", [])
    validated = [v for v in validations if v.get("status") == "passed"]
    failed = [v for v in validations if v.get("status") == "failed"]
    stale = [v for v in validations if v.get("status") == "stale"]
    required_files: list[str] = []
    if current_work:
        required_files.extend(current_work.get("related_file_paths", []))
    if remaining:
        required_files.extend(remaining[0].get("related_file_paths", []) if remaining else [])
    required_files = sorted(set(required_files))
    artifact_paths = {}
    for fname in WORK_STATE_FILES.values():
        p = op_dir / fname
        artifact_paths[fname] = str(p)
    return {
        "operation_id": operation_id,
        "repository_root": repository_root,
        "work_state_revision": rev,
        "operation_status": work_status.get("operation_status", "in_progress"),
        "updated_plan": updated_plan,
        "completed_work": completed_work,
        "current_work": current_work,
        "remaining_work": remaining,
        "validated_results": validated,
        "failed_results": failed,
        "stale_results": stale,
        "unresolved_items": work_status.get("unresolved_items", []),
        "next_exact_action": work_status.get("next_exact_action"),
        "required_files_to_inspect_next": required_files,
        "operation_artifact_paths": artifact_paths,
    }

def _load_current_state(op_dir: Path):
    updated = _load_json(op_dir / WORK_STATE_FILES["updated_plan"])
    work = _load_json(op_dir / WORK_STATE_FILES["work_status"])
    validation = _load_json(op_dir / WORK_STATE_FILES["validation_report"])
    continuation = _load_json(op_dir / WORK_STATE_FILES["continuation_package"])
    if not (updated.get("operation_id") == work.get("operation_id") == validation.get("operation_id") == continuation.get("operation_id")):
        raise ReviewError("operation_id mismatch between artifacts")
    revs = [updated.get("work_state_revision"), work.get("work_state_revision"), validation.get("work_state_revision"), continuation.get("work_state_revision")]
    if len(set(revs)) != 1:
        raise ReviewError(f"revision mismatch: {revs}")
    for step in updated.get("steps", []):
        _validate_step(step)
    if work.get("operation_status") not in OPERATION_STATUSES:
        raise ReviewError(f"invalid operation_status {work.get('operation_status')}")
    step_ids = {s["step_id"] for s in updated.get("steps", [])}
    for sid in work.get("completed_step_ids", []):
        if sid not in step_ids:
            raise ReviewError(f"unknown step ref in completed: {sid}")
    if work.get("current_step_id") and work["current_step_id"] not in step_ids:
        raise ReviewError(f"unknown current_step_id: {work['current_step_id']}")
    for sid in work.get("remaining_step_ids", []):
        if sid not in step_ids:
            raise ReviewError(f"unknown remaining ref: {sid}")
    for v in validation.get("validations", []):
        for cs in v.get("covered_step_ids", []):
            if cs not in step_ids:
                raise ReviewError(f"unknown step ref in validation {v.get('validation_id')}: {cs}")
    return updated, work, validation, continuation

def initialize_work_state(*, operation_id: str, plan_id: str, repository_root: str, evaluated_plan: str) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    if not op_dir.exists():
        raise ReviewError(f"unknown operation_id: {operation_id}")
    if not (op_dir / "operation-config.json").exists():
        raise ReviewError(f"unknown operation_id: {operation_id}")
    lock = _acquire_lock(op_dir)
    try:
        cont_path = op_dir / WORK_STATE_FILES["continuation_package"]
        if cont_path.exists():
            try:
                existing = _load_json(cont_path)
                if (op_dir / WORK_STATE_FILES["work_status"]).exists():
                    return existing
            except ReviewError:
                pass
        steps = _derive_steps(evaluated_plan)
        first_ready = None
        for step in steps:
            if not step["dependency_step_ids"]:
                first_ready = step["step_id"]
                break
        if not first_ready and steps:
            first_ready = steps[0]["step_id"]
        for step in steps:
            if step["step_id"] == first_ready:
                step["status"] = "in_progress"
                break
        rev = 1
        now = _now_iso()
        updated_plan = {
            "operation_id": operation_id,
            "plan_id": plan_id,
            "work_state_revision": rev,
            "steps": steps,
            "last_updated_at": now,
        }
        work_status = {
            "operation_id": operation_id,
            "work_state_revision": rev,
            "operation_status": "in_progress" if steps else "completed",
            "completed_step_ids": [],
            "current_step_id": first_ready,
            "remaining_step_ids": [s["step_id"] for s in steps],
            "changed_files": [],
            "created_files": [],
            "deleted_files": [],
            "unresolved_items": [],
            "last_completed_action": None,
            "next_exact_action": f"Begin {first_ready}" if first_ready else None,
            "last_updated_at": now,
        }
        validation_report = {
            "operation_id": operation_id,
            "work_state_revision": rev,
            "validations": [],
        }
        continuation = _build_continuation_pkg(
            operation_id=operation_id,
            repository_root=repository_root,
            updated_plan=updated_plan,
            work_status=work_status,
            validation_report=validation_report,
            op_dir=op_dir,
        )
        for sec in steps:
            _validate_step(sec)
        if work_status["current_step_id"] and work_status["current_step_id"] not in {s["step_id"] for s in steps}:
            raise ReviewError("current_step_id unknown")
        _check_no_conversation(updated_plan)
        _check_no_conversation(work_status)
        _check_no_conversation(validation_report)
        _check_no_conversation(continuation)
        tmp_files = {}
        for key, fname in WORK_STATE_FILES.items():
            data_map = {"updated_plan": updated_plan, "work_status": work_status, "validation_report": validation_report, "continuation_package": continuation}[key]
            path = op_dir / fname
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data_map, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
            json.loads(tmp.read_text(encoding="utf-8"))
            tmp_files[key] = (tmp, path)
        for tmp, dest in tmp_files.values():
            os.replace(tmp, dest)
        return continuation
    finally:
        _release_lock(lock)

def update_work_state(*, operation_id: str, expected_revision: int, completed_step_ids: list[str], current_step_id: str | None, changed_files: list[str], created_files: list[str], deleted_files: list[str], validation_records: list[dict], unresolved_items: list[dict], last_completed_action: str | None, next_exact_action: str | None) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    if not op_dir.exists():
        raise ReviewError(f"unknown operation_id: {operation_id}")
    lock = _acquire_lock(op_dir)
    try:
        updated, work, validation_report, continuation = _load_current_state(op_dir)
        current_rev = updated.get("work_state_revision")
        if expected_revision != current_rev:
            raise ReviewError(f"stale expected_revision: got {expected_revision}, current {current_rev}")
        step_ids_set = {s["step_id"] for s in updated["steps"]}
        for sid in completed_step_ids:
            if sid not in step_ids_set:
                raise ReviewError(f"unknown step_id: {sid}")
        if current_step_id and current_step_id not in step_ids_set:
            raise ReviewError(f"unknown current_step_id: {current_step_id}")
        for rec in validation_records:
            _validate_validation_rec(rec)
        existing_validations = list(validation_report.get("validations", []))
        repo_root = Path(continuation.get("repository_root") or str(_get_operations_root().parent))
        if not repo_root.exists():
            repo_root = Path.cwd()
        enriched_new = []
        for rec in validation_records:
            enriched = dict(rec)
            if "file_states" not in enriched or not enriched["file_states"]:
                fs = {}
                for fp in enriched.get("covered_file_paths", []):
                    fs[fp] = _file_state_for_path(repo_root, fp)
                enriched["file_states"] = fs
            if "recorded_at" not in enriched:
                enriched["recorded_at"] = _now_iso()
            if "last_verified_at" not in enriched:
                enriched["last_verified_at"] = _now_iso()
            enriched_new.append(enriched)
        merged_validations = existing_validations + enriched_new
        latest_by_id: dict[str, dict] = {}
        for v in merged_validations:
            vid = v["validation_id"]
            if vid not in latest_by_id or v.get("recorded_at", "") >= latest_by_id[vid].get("recorded_at", ""):
                latest_by_id[vid] = v
        steps_by_id = {s["step_id"]: s for s in updated["steps"]}
        for sid in completed_step_ids:
            s = steps_by_id[sid]
            for dep in s.get("dependency_step_ids", []):
                if dep not in completed_step_ids and dep not in work.get("completed_step_ids", []):
                    raise ReviewError(f"dependency must precede: {sid} depends on {dep}")
        new_statuses: dict[str, str] = {}
        for step in updated["steps"]:
            sid = step["step_id"]
            old_status = step["status"]
            new_status = old_status
            if sid in completed_step_ids:
                covering_passed = False
                for v in enriched_new:
                    if sid in v.get("covered_step_ids", []) and v.get("status") == "passed":
                        covering_passed = True
                        break
                if not covering_passed:
                    for v in merged_validations:
                        if sid in v.get("covered_step_ids", []) and v.get("status") == "passed":
                            latest = latest_by_id.get(v["validation_id"])
                            if latest and latest.get("status") == "passed":
                                covering_passed = True
                                break
                new_status = "completed" if covering_passed else "completed_unverified"
            else:
                for v in enriched_new:
                    if sid in v.get("covered_step_ids", []) and v.get("status") == "failed":
                        if old_status in {"completed", "completed_unverified", "in_progress"}:
                            new_status = "in_progress"
                        break
            if new_status != old_status:
                allowed = ALLOWED_TRANSITIONS.get(old_status, set())
                if old_status == "pending" and new_status in {"completed", "completed_unverified"}:
                    if new_status not in ALLOWED_TRANSITIONS.get("in_progress", set()):
                        raise ReviewError(f"invalid transition {old_status} -> {new_status} for {sid}")
                elif new_status not in allowed and not (old_status == "pending" and new_status == "in_progress"):
                    if not (old_status == "pending" and new_status in {"completed", "completed_unverified"}):
                        raise ReviewError(f"invalid transition {old_status} -> {new_status} for {sid}")
            new_statuses[sid] = new_status
        for step in updated["steps"]:
            sid = step["step_id"]
            for dep in step.get("dependency_step_ids", []):
                dep_status = new_statuses.get(dep, steps_by_id[dep]["status"])
                if dep_status == "invalidated":
                    if new_statuses[sid] in {"completed", "pending", "completed_unverified"}:
                        new_statuses[sid] = "invalidated"
        new_steps = []
        for s in updated["steps"]:
            ns = dict(s)
            ns["status"] = new_statuses.get(s["step_id"], s["status"])
            new_steps.append(ns)
        new_rev = current_rev + 1
        now = _now_iso()
        new_updated_plan = {
            "operation_id": operation_id,
            "plan_id": updated.get("plan_id"),
            "work_state_revision": new_rev,
            "steps": new_steps,
            "last_updated_at": now,
        }
        prev_changed = set(work.get("changed_files", []))
        prev_created = set(work.get("created_files", []))
        prev_deleted = set(work.get("deleted_files", []))
        all_changed = sorted(prev_changed | set(changed_files or []))
        all_created = sorted(prev_created | set(created_files or []))
        all_deleted = sorted(prev_deleted | set(deleted_files or []))
        all_unresolved = list(work.get("unresolved_items", [])) + (unresolved_items or [])
        final_completed = [s["step_id"] for s in new_steps if s["status"] == "completed"]
        remaining_ids = [s["step_id"] for s in new_steps if s["status"] != "completed"]
        new_current = current_step_id
        if not new_current:
            for sid in remaining_ids:
                deps = steps_by_id.get(sid, {}).get("dependency_step_ids", [])
                if all(d in final_completed for d in deps):
                    new_current = sid
                    break
        if new_current and new_current in final_completed:
            new_current = None
            for sid in remaining_ids:
                deps = steps_by_id.get(sid, {}).get("dependency_step_ids", [])
                if all(d in final_completed for d in deps):
                    new_current = sid
                    break
        for s in new_steps:
            if s["step_id"] == new_current and s["status"] == "pending":
                s["status"] = "in_progress"
        remaining_ids = [s["step_id"] for s in new_steps if s["status"] != "completed"]
        if not remaining_ids and not all_unresolved and all(v.get("status") not in {"failed", "stale"} for v in merged_validations) and all(s["status"] == "completed" for s in new_steps):
            op_status = "completed"
            final_cur = None
            final_remaining: list[str] = []
            final_next = None
        else:
            op_status = "in_progress"
            final_cur = new_current
            final_remaining = remaining_ids
            if next_exact_action:
                final_next = next_exact_action
            elif final_cur:
                final_next = f"Continue {final_cur}"
            elif all_unresolved:
                final_next = "Resolve unresolved items"
            elif any(v.get("status") in {"failed", "stale"} for v in merged_validations):
                final_next = "Rerun failed/stale validations"
            else:
                final_next = "Continue remaining work"
        new_work_status = {
            "operation_id": operation_id,
            "work_state_revision": new_rev,
            "operation_status": op_status,
            "completed_step_ids": final_completed,
            "current_step_id": final_cur,
            "remaining_step_ids": final_remaining,
            "changed_files": all_changed,
            "created_files": all_created,
            "deleted_files": all_deleted,
            "unresolved_items": all_unresolved,
            "last_completed_action": last_completed_action,
            "next_exact_action": final_next,
            "last_updated_at": now,
        }
        if new_work_status["operation_status"] == "completed":
            if all_unresolved:
                new_work_status["operation_status"] = "in_progress"
                new_work_status["next_exact_action"] = "Resolve unresolved items"
            elif any(v.get("status") in {"failed", "stale"} for v in merged_validations):
                new_work_status["operation_status"] = "in_progress"
                new_work_status["next_exact_action"] = "Rerun failed/stale validations"
        new_validation_report = {
            "operation_id": operation_id,
            "work_state_revision": new_rev,
            "validations": merged_validations,
        }
        new_continuation = _build_continuation_pkg(
            operation_id=operation_id,
            repository_root=continuation.get("repository_root", str(repo_root)),
            updated_plan=new_updated_plan,
            work_status=new_work_status,
            validation_report=new_validation_report,
            op_dir=op_dir,
        )
        _check_no_conversation(new_updated_plan)
        _check_no_conversation(new_work_status)
        _check_no_conversation(new_validation_report)
        _check_no_conversation(new_continuation)
        tmp_map = {}
        for data_obj, fname in [
            (new_updated_plan, WORK_STATE_FILES["updated_plan"]),
            (new_work_status, WORK_STATE_FILES["work_status"]),
            (new_validation_report, WORK_STATE_FILES["validation_report"]),
            (new_continuation, WORK_STATE_FILES["continuation_package"]),
        ]:
            path = op_dir / fname
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data_obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
            json.loads(tmp.read_text(encoding="utf-8"))
            tmp_map[fname] = (tmp, path)
        for tmp, dest in tmp_map.values():
            os.replace(tmp, dest)
        return new_continuation
    finally:
        _release_lock(lock)

def resume_operation(operation_id: str) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    if not op_dir.exists():
        raise ReviewError(f"unknown operation_id: {operation_id}")
    lock = _acquire_lock(op_dir)
    try:
        updated, work, validation_report, continuation = _load_current_state(op_dir)
        tracked_files = set()
        tracked_files.update(work.get("changed_files", []))
        tracked_files.update(work.get("created_files", []))
        for v in validation_report.get("validations", []):
            tracked_files.update(v.get("covered_file_paths", []))
        for step in updated.get("steps", []):
            tracked_files.update(step.get("related_file_paths", []))
        repo_root = Path(continuation.get("repository_root") or str(_get_operations_root().parent))
        if not repo_root.exists():
            repo_root = Path.cwd()
        latest_by_id: dict[str, dict] = {}
        for v in validation_report.get("validations", []):
            vid = v["validation_id"]
            if vid not in latest_by_id or v.get("recorded_at", "") >= latest_by_id[vid].get("recorded_at", ""):
                latest_by_id[vid] = v
        affected_step_ids: set[str] = set()
        stale_vids: set[str] = set()
        unresolved_missing: list[dict] = []
        for vid, v in latest_by_id.items():
            if v.get("status") != "passed":
                continue
            file_states = v.get("file_states", {})
            for rel_path, stored_state in file_states.items():
                live_state = _file_state_for_path(repo_root, rel_path)
                if stored_state.get("exists") != live_state.get("exists"):
                    stale_vids.add(vid)
                    affected_step_ids.update(v.get("covered_step_ids", []))
                    break
                if stored_state.get("exists") and live_state.get("exists"):
                    if stored_state.get("size_bytes") != live_state.get("size_bytes"):
                        stale_vids.add(vid)
                        affected_step_ids.update(v.get("covered_step_ids", []))
                        break
                    try:
                        import datetime as _dt
                        sm = _dt.datetime.fromisoformat(stored_state.get("modified_time", ""))
                        lm = _dt.datetime.fromisoformat(live_state.get("modified_time", ""))
                        if lm > sm:
                            stale_vids.add(vid)
                            affected_step_ids.update(v.get("covered_step_ids", []))
                            break
                    except Exception:
                        pass
        for fp in tracked_files:
            fp_path = repo_root / fp if not Path(fp).is_absolute() else Path(fp)
            if not fp_path.exists():
                for v in latest_by_id.values():
                    if fp in v.get("file_states", {}) and v["file_states"][fp].get("exists"):
                        if v.get("status") == "passed":
                            stale_vids.add(v["validation_id"])
                            affected_step_ids.update(v.get("covered_step_ids", []))
                        unresolved_missing.append({"type": "missing_file", "file_path": fp})
                        break
        if not stale_vids and not unresolved_missing:
            return continuation
        new_validations: list[dict] = []
        for v in validation_report.get("validations", []):
            if v["validation_id"] in stale_vids and v.get("status") == "passed" and latest_by_id.get(v["validation_id"]) is v:
                new_v = dict(v)
                new_v["status"] = "stale"
                new_v["last_verified_at"] = _now_iso()
                new_validations.append(new_v)
            else:
                new_validations.append(v)
        new_steps = []
        for s in updated.get("steps", []):
            ns = dict(s)
            if s["step_id"] in affected_step_ids and s["status"] == "completed":
                ns["status"] = "in_progress"
            new_steps.append(ns)
        new_rev = updated.get("work_state_revision", 1) + 1
        now = _now_iso()
        new_updated = {
            "operation_id": operation_id,
            "plan_id": updated.get("plan_id"),
            "work_state_revision": new_rev,
            "steps": new_steps,
            "last_updated_at": now,
        }
        new_work = dict(work)
        new_work["work_state_revision"] = new_rev
        new_work["completed_step_ids"] = [s["step_id"] for s in new_steps if s["status"] == "completed"]
        new_work["remaining_step_ids"] = [s["step_id"] for s in new_steps if s["status"] != "completed"]
        new_work["operation_status"] = "in_progress"
        new_work["last_updated_at"] = now
        if affected_step_ids:
            new_work["next_exact_action"] = f"Rerun affected validation for steps {sorted(affected_step_ids)}"
        if unresolved_missing:
            new_work["unresolved_items"] = list(work.get("unresolved_items", [])) + unresolved_missing
        new_validation_report = {
            "operation_id": operation_id,
            "work_state_revision": new_rev,
            "validations": new_validations,
        }
        new_continuation = _build_continuation_pkg(
            operation_id=operation_id,
            repository_root=continuation.get("repository_root"),
            updated_plan=new_updated,
            work_status=new_work,
            validation_report=new_validation_report,
            op_dir=op_dir,
        )
        tmp_map = {}
        for data_obj, fname in [
            (new_updated, WORK_STATE_FILES["updated_plan"]),
            (new_work, WORK_STATE_FILES["work_status"]),
            (new_validation_report, WORK_STATE_FILES["validation_report"]),
            (new_continuation, WORK_STATE_FILES["continuation_package"]),
        ]:
            path = op_dir / fname
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data_obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
            json.loads(tmp.read_text(encoding="utf-8"))
            tmp_map[fname] = (tmp, path)
        for tmp, dest in tmp_map.values():
            os.replace(tmp, dest)
        return new_continuation
    finally:
        _release_lock(lock)

def load_continuation_package(operation_id: str) -> dict[str, Any]:
    op_dir = _operation_dir(operation_id)
    cont_path = op_dir / WORK_STATE_FILES["continuation_package"]
    return _load_json(cont_path)
