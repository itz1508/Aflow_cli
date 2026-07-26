"""Stale revalidation proof.

Records two independent validations (each covering a distinct step and a
distinct file) as passing, then modifies ONLY one file inside an isolated
temporary repository root. Proves that:

  1. resume marks ONLY the affected validation stale and reopens ONLY the
     affected step; the untouched validation/step stay passed/completed.
  2. Restoring the file and rerunning ONLY the affected validation returns that
     step to completed without touching the other validation.
  3. The controlled modification happens in a temp repo; the real repository
     source (work_state.py) is byte-identical before and after.

Sanitized evidence -> release-evidence/continuity-proof/stale-revalidation/proof.json

Run: uv run python scripts/stale_revalidation_proof.py
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "release-evidence" / "continuity-proof" / "stale-revalidation" / "proof.json"
REAL_SOURCE_SENTINEL = REPO_ROOT / "src" / "aflow_cli" / "work_state.py"

PLAN = (
    "## Implement module A\n"
    "Objective: build module A.\n\n"
    "## Implement module B\n"
    "Objective: build module B.\n"
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _sanitize(value):
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    repo = str(REPO_ROOT)
    if isinstance(value, str):
        out = value
        for needle, token in ((repo, "<REPO>"), (home, "<HOME>")):
            if needle:
                out = out.replace(needle, token).replace(needle.replace("\\", "/"), token)
        if user:
            out = out.replace(user, "<USER>")
        return out
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _record(vid: str, step_id: str, rel_path: str, repo: Path) -> dict:
    target = repo / rel_path
    stat = target.stat()
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc).isoformat()
    return {
        "validation_id": vid,
        "covered_step_ids": [step_id],
        "covered_file_paths": [rel_path],
        "method": "command",
        "command": "python -c pass",
        "exit_code": 0,
        "status": "passed",
        "captured_output_path": f"evidence/{vid}.txt",
        "evidence_paths": [],
        "file_states": {rel_path: {"exists": True, "size_bytes": stat.st_size, "modified_time": mtime}},
        "recorded_at": _now(),
        "last_verified_at": _now(),
    }


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="aflow_stale_"))
    repo = workdir / "repo"
    repo.mkdir(parents=True)
    os.environ["AFLOW_OPERATIONS_ROOT"] = str(workdir / "ops")

    file_a = repo / "module_a.txt"
    file_b = repo / "module_b.txt"
    original_a = "module A original contents\n"
    file_a.write_text(original_a, encoding="utf-8")
    file_b.write_text("module B original contents\n", encoding="utf-8")

    real_source_hash_before = hashlib.sha256(REAL_SOURCE_SENTINEL.read_bytes()).hexdigest()

    from aflow_cli.core import review_plan
    from aflow_cli.work_state import _operation_dir, resume_operation, update_work_state

    oid = "stale-revalidation-proof"
    review_plan(original_plan=PLAN, plan_id=oid, operation_id=oid, repository_root=str(repo), user_prompt="proof", force_canonical=True)
    op_dir = _operation_dir(oid)
    steps = json.loads((op_dir / "updated-plan.json").read_text(encoding="utf-8"))["steps"]
    sid_a, sid_b = steps[0]["step_id"], steps[1]["step_id"]

    (op_dir / "evidence").mkdir(exist_ok=True)
    for vid in ("val-a", "val-b"):
        (op_dir / "evidence" / f"{vid}.txt").write_text("ok", encoding="utf-8")

    # Baseline: both steps validated + completed in one revision.
    cont = update_work_state(
        operation_id=oid, expected_revision=1,
        completed_step_ids=[sid_a, sid_b], current_step_id=None,
        changed_files=["module_a.txt", "module_b.txt"], created_files=[], deleted_files=[],
        validation_records=[_record("val-a", sid_a, "module_a.txt", repo), _record("val-b", sid_b, "module_b.txt", repo)],
        unresolved_items=[], last_completed_action="implemented A and B", next_exact_action=None,
    )
    baseline_steps = {s["step_id"]: s["status"] for s in cont["updated_plan"]["steps"]}
    rev_baseline = cont["work_state_revision"]

    evidence: dict = {"proof_type": "stale-revalidation"}
    evidence["baseline"] = {
        "revision": rev_baseline,
        "step_a_status": baseline_steps[sid_a],
        "step_b_status": baseline_steps[sid_b],
        "both_completed": baseline_steps[sid_a] == "completed" and baseline_steps[sid_b] == "completed",
    }

    # Controlled modification: change ONLY file A inside the temp repo.
    time.sleep(0.05)
    file_a.write_text(original_a + "APPENDED after validation to change size and mtime\n", encoding="utf-8")

    cont_after = resume_operation(oid)
    steps_after = {s["step_id"]: s["status"] for s in cont_after["updated_plan"]["steps"]}
    validations_after = {v["validation_id"]: v["status"] for v in json.loads((op_dir / "validation-report.json").read_text(encoding="utf-8"))["validations"]}
    evidence["after_modification"] = {
        "revision": cont_after["work_state_revision"],
        "val_a_status": validations_after.get("val-a"),
        "val_b_status": validations_after.get("val-b"),
        "step_a_status": steps_after[sid_a],
        "step_b_status": steps_after[sid_b],
        "next_exact_action": cont_after.get("next_exact_action"),
        "only_a_stale": validations_after.get("val-a") == "stale" and validations_after.get("val-b") == "passed",
        "only_step_a_reopened": steps_after[sid_a] == "in_progress" and steps_after[sid_b] == "completed",
    }

    # Restore file A, then rerun ONLY the affected validation (val-a).
    file_a.write_text(original_a, encoding="utf-8")
    rev_now = cont_after["work_state_revision"]
    cont_rerun = update_work_state(
        operation_id=oid, expected_revision=rev_now,
        completed_step_ids=[sid_a], current_step_id=None,
        changed_files=[], created_files=[], deleted_files=[],
        validation_records=[_record("val-a", sid_a, "module_a.txt", repo)],
        unresolved_items=[], last_completed_action="reran affected validation only", next_exact_action=None,
    )
    steps_rerun = {s["step_id"]: s["status"] for s in cont_rerun["updated_plan"]["steps"]}
    validations_rerun = {v["validation_id"]: v["status"] for v in json.loads((op_dir / "validation-report.json").read_text(encoding="utf-8"))["validations"]}
    evidence["after_rerun"] = {
        "revision": cont_rerun["work_state_revision"],
        "val_a_status": validations_rerun.get("val-a"),
        "val_b_status": validations_rerun.get("val-b"),
        "step_a_status": steps_rerun[sid_a],
        "step_b_status": steps_rerun[sid_b],
        "step_a_recompleted": steps_rerun[sid_a] == "completed",
        "val_b_never_reran": validations_rerun.get("val-b") == "passed",
    }

    real_source_hash_after = hashlib.sha256(REAL_SOURCE_SENTINEL.read_bytes()).hexdigest()
    evidence["source_integrity"] = {
        "controlled_file_restored": file_a.read_text(encoding="utf-8") == original_a,
        "real_repository_source_unchanged": real_source_hash_before == real_source_hash_after,
        "modification_confined_to_temp_repo": str(repo).startswith(tempfile.gettempdir()) or "Temp" in str(repo),
    }

    checks = {
        "baseline_both_completed": evidence["baseline"]["both_completed"],
        "only_affected_validation_stale": evidence["after_modification"]["only_a_stale"],
        "only_affected_step_reopened": evidence["after_modification"]["only_step_a_reopened"],
        "affected_step_recompleted_after_rerun": evidence["after_rerun"]["step_a_recompleted"],
        "unaffected_validation_never_reran": evidence["after_rerun"]["val_b_never_reran"],
        "controlled_file_restored": evidence["source_integrity"]["controlled_file_restored"],
        "real_source_undamaged": evidence["source_integrity"]["real_repository_source_unchanged"],
    }
    evidence["checks"] = checks
    evidence["all_checks_passed"] = all(checks.values())

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_sanitize(evidence), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, EVIDENCE)
    print(json.dumps(_sanitize(evidence), indent=2, sort_keys=True))
    return 0 if evidence["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
