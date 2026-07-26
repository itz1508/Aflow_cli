from __future__ import annotations
import json, os, pathlib, shutil, threading, time
import pytest
from aflow.core import review_plan
from aflow.work_state import _operation_dir, load_continuation_package, resume_operation, update_work_state
from aflow.cli import main as cli_main
from aflow.mcp_server import aflow_resume, aflow_update_work_state

PLAN = "## Step One\nImplement feature\n## Step Two\nValidate feature\n## Step Three\nFinalize\nObjective: test continuity\nSuccess criteria: all steps\nValidation: pytest\nFixture: sample\n"

def _op_id(name):
    return f"continuity-{name}"

def _clean_op(op_id):
    d = _operation_dir(op_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

def _create_op(suffix):
    oid = _op_id(suffix)
    _clean_op(oid)
    b = review_plan(original_plan=PLAN, plan_id=oid, operation_id=oid, repository_root=r"D:\Dev\Aflow_cli", user_prompt="test", force_canonical=True)
    assert b["manifest"]["stages_executed"] == 11
    d = _operation_dir(oid)
    assert (d / "updated-plan.json").exists()
    return oid, d

def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def test_A_initial_artifacts():
    oid, d = _create_op("A")
    for fn in ["updated-plan.json", "work-status.json", "validation-report.json", "continuation-package.json"]:
        assert (d / fn).exists()
    upd = json.loads((d / "updated-plan.json").read_text())
    assert upd["work_state_revision"] == 1

def test_B_stable_step_ids():
    oid, d = _create_op("B")
    upd1 = json.loads((d / "updated-plan.json").read_text())
    ids1 = [s["step_id"] for s in upd1["steps"]]
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    rec = {"validation_id": "val-001", "covered_step_ids": [ids1[0]], "covered_file_paths": [], "method": "command", "command": "pytest -q", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": _now(), "last_verified_at": _now()}
    update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[ids1[0]], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="did", next_exact_action="next")
    upd2 = json.loads((d / "updated-plan.json").read_text())
    ids2 = [s["step_id"] for s in upd2["steps"]]
    assert ids1 == ids2
    resume_operation(oid)
    upd3 = json.loads((d / "updated-plan.json").read_text())
    ids3 = [s["step_id"] for s in upd3["steps"]]
    assert ids1 == ids3

def test_C_first_ready():
    oid, d = _create_op("C")
    work = json.loads((d / "work-status.json").read_text())
    upd = json.loads((d / "updated-plan.json").read_text())
    first = upd["steps"][0]
    assert not first["dependency_step_ids"]
    assert work["current_step_id"] == first["step_id"]
    assert first["status"] == "in_progress"

def test_D_validated_completes():
    oid, d = _create_op("D")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": ["src/aflow/core.py"], "method": "command", "command": "pytest -q", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {"src/aflow/core.py": {"exists": True, "size_bytes": 100, "modified_time": now}}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("passed", encoding="utf-8")
    cont = update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=["src/aflow/core.py"], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="completed", next_exact_action="next")
    assert cont["work_state_revision"] == 2
    assert cont["updated_plan"]["steps"][0]["status"] == "completed"

def test_E_unsupported_becomes_unverified():
    oid, d = _create_op("E")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    cont = update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[], unresolved_items=[], last_completed_action="claimed", next_exact_action="next")
    step = [s for s in cont["updated_plan"]["steps"] if s["step_id"] == sid][0]
    assert step["status"] == "completed_unverified"

def test_F_failed_reopens():
    oid, d = _create_op("F")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec_pass = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": ["src/aflow/core.py"], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {"src/aflow/core.py": {"exists": True, "size_bytes": 10, "modified_time": now}}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec_pass], unresolved_items=[], last_completed_action="done", next_exact_action="next")
    now2 = _now()
    rec_fail = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": ["src/aflow/core.py"], "method": "command", "command": "pytest", "exit_code": 1, "status": "failed", "captured_output_path": "evidence/val-001-fail.txt", "evidence_paths": [], "file_states": {"src/aflow/core.py": {"exists": True, "size_bytes": 10, "modified_time": now2}}, "recorded_at": now2, "last_verified_at": now2}
    (d / "evidence" / "val-001-fail.txt").write_text("failed", encoding="utf-8")
    cont2 = update_work_state(operation_id=oid, expected_revision=2, completed_step_ids=[], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec_fail], unresolved_items=[], last_completed_action=None, next_exact_action="rerun")
    step = [s for s in cont2["updated_plan"]["steps"] if s["step_id"] == sid][0]
    assert step["status"] == "in_progress"

def test_G_stale_reopens_only_covered():
    oid, d = _create_op("G")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid1 = upd["steps"][0]["step_id"]
    repo = pathlib.Path(r"D:\Dev\Aflow_cli")
    tmp_file = repo / "C_temp_stale_test.txt"
    tmp_file.write_text("original", encoding="utf-8")
    now = _now()
    size_orig = tmp_file.stat().st_size
    rec = {"validation_id": "val-stale-001", "covered_step_ids": [sid1], "covered_file_paths": ["C_temp_stale_test.txt"], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-stale.txt", "evidence_paths": [], "file_states": {"C_temp_stale_test.txt": {"exists": True, "size_bytes": size_orig, "modified_time": now}}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-stale.txt").write_text("ok", encoding="utf-8")
    update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid1], current_step_id=None, changed_files=["C_temp_stale_test.txt"], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="done", next_exact_action="next")
    time.sleep(0.1)
    tmp_file.write_text("modified content longer to change size", encoding="utf-8")
    cont = resume_operation(oid)
    step1 = [s for s in cont["updated_plan"]["steps"] if s["step_id"] == sid1][0]
    assert step1["status"] == "in_progress"
    tmp_file.unlink(missing_ok=True)

def test_H_resume_only_tracked():
    oid, d = _create_op("H")
    repo = pathlib.Path(r"D:\Dev\Aflow_cli")
    untracked = repo / "C_temp_untracked.txt"
    untracked.write_text("original", encoding="utf-8")
    cont1 = resume_operation(oid)
    rev1 = cont1["work_state_revision"]
    untracked.write_text("modified", encoding="utf-8")
    cont2 = resume_operation(oid)
    assert cont2["work_state_revision"] == rev1
    untracked.unlink(missing_ok=True)

def test_I_cli_mcp_equiv(tmp_path):
    oid_cli, d_cli = _create_op("I_cli")
    oid_mcp, d_mcp = _create_op("I_mcp")
    upd_cli = json.loads((d_cli / "updated-plan.json").read_text())
    sid_cli = upd_cli["steps"][0]["step_id"]
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": [sid_cli], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d_cli / "evidence").mkdir(exist_ok=True)
    (d_cli / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    (d_mcp / "evidence").mkdir(exist_ok=True)
    (d_mcp / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    upd_json = {"expected_revision": 1, "completed_step_ids": [sid_cli], "current_step_id": None, "changed_files": [], "created_files": [], "deleted_files": [], "validation_records": [rec], "unresolved_items": [], "last_completed_action": "cli done", "next_exact_action": "next"}
    upd_file = tmp_path / "upd.json"
    upd_file.write_text(json.dumps(upd_json), encoding="utf-8")
    assert cli_main(["progress", "--operation-id", oid_cli, "--update-file", str(upd_file)]) == 0
    cont_mcp = aflow_update_work_state(operation_id=oid_mcp, expected_revision=1, completed_step_ids=[sid_cli], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="mcp done", next_exact_action="next")
    cli_cont = json.loads((d_cli / "continuation-package.json").read_text())
    assert cli_cont["work_state_revision"] == cont_mcp["work_state_revision"]
    assert cli_cont["updated_plan"]["steps"][0]["status"] == cont_mcp["updated_plan"]["steps"][0]["status"]

def test_J_cli_mcp_resume_equiv():
    oid, d = _create_op("J")
    import io
    from contextlib import redirect_stdout
    f = io.StringIO()
    with redirect_stdout(f):
        cli_main(["resume", "--operation-id", oid])
    cli_out = json.loads(f.getvalue())
    mcp_out = aflow_resume(operation_id=oid)
    assert cli_out["operation_id"] == mcp_out["operation_id"]
    assert cli_out["work_state_revision"] == mcp_out["work_state_revision"]

def test_K_no_canonical_rerun():
    oid, d = _create_op("K")
    canonical = ["original-plan.md", "operation-config.json", "gap-resolution-history.json"]
    mtimes = {}
    for fn in canonical:
        p = d / fn
        if p.exists():
            mtimes[fn] = p.stat().st_mtime
    resume_operation(oid)
    for fn, old in mtimes.items():
        assert (d / fn).stat().st_mtime == old

def test_L_idempotent_resume():
    oid, d = _create_op("L")
    c1 = resume_operation(oid)
    c2 = resume_operation(oid)
    assert c1["work_state_revision"] == c2["work_state_revision"]

def test_M_revision_increment():
    oid, d = _create_op("M")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    c1 = update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="done", next_exact_action="next")
    assert c1["work_state_revision"] == 2
    now2 = _now()
    rec2 = {"validation_id": "val-002", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-002.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now2, "last_verified_at": now2}
    (d / "evidence" / "val-002.txt").write_text("ok2", encoding="utf-8")
    c2 = update_work_state(operation_id=oid, expected_revision=2, completed_step_ids=[], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec2], unresolved_items=[], last_completed_action="done2", next_exact_action="next2")
    assert c2["work_state_revision"] == 3

def test_N_stale_revision_rejected():
    oid, d = _create_op("N")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="done", next_exact_action="next")
    with pytest.raises(Exception) as exc:
        update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[], unresolved_items=[], last_completed_action=None, next_exact_action=None)
    assert "stale expected_revision" in str(exc.value)
    cont = load_continuation_package(oid)
    assert cont["work_state_revision"] == 2

def test_O_concurrent_protection():
    oid, d = _create_op("O")
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec1 = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    rec2 = {"validation_id": "val-002", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-002.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    (d / "evidence" / "val-002.txt").write_text("ok", encoding="utf-8")
    errors = []
    def try_update(rec, rev):
        try:
            update_work_state(operation_id=oid, expected_revision=rev, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="done", next_exact_action="next")
        except Exception as e:
            errors.append(str(e))
    t1 = threading.Thread(target=try_update, args=(rec1, 1))
    t2 = threading.Thread(target=try_update, args=(rec2, 1))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(errors) == 1
    assert "stale" in errors[0] or "locked" in errors[0]
    cont = load_continuation_package(oid)
    assert cont["work_state_revision"] == 2

def test_P_atomic_preservation():
    oid, d = _create_op("P")
    tmp = d / "work-status.json.tmp"
    tmp.write_text("{ invalid", encoding="utf-8")
    cont = load_continuation_package(oid)
    assert cont["work_state_revision"] == 1
    tmp.unlink(missing_ok=True)
    upd = json.loads((d / "updated-plan.json").read_text())
    sid = upd["steps"][0]["step_id"]
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    cont = update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=[sid], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action="done", next_exact_action="next")
    assert cont["work_state_revision"] == 2

def test_Q_unknown_refs():
    oid, d = _create_op("Q")
    now = _now()
    rec = {"validation_id": "val-001", "covered_step_ids": ["unknown-step"], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": "evidence/val-001.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
    (d / "evidence").mkdir(exist_ok=True)
    (d / "evidence" / "val-001.txt").write_text("ok", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=["unknown-step"], current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=[rec], unresolved_items=[], last_completed_action=None, next_exact_action=None)
    assert "unknown step" in str(exc.value).lower()
    (d / "updated-plan.json").write_text("{ invalid", encoding="utf-8")
    with pytest.raises(Exception) as exc2:
        resume_operation(oid)
    assert "malformed" in str(exc2.value).lower()
    _clean_op(oid)

def test_R_resume_operation_id_only():
    oid, d = _create_op("R")
    import subprocess, sys
    cmd = [sys.executable, "-c", f"from aflow.work_state import resume_operation; pkg=resume_operation(operation_id='{oid}'); print(pkg['operation_id'])"]
    result = subprocess.run(cmd, cwd=r"D:\Dev\Aflow_cli", capture_output=True, text=True)
    assert result.returncode == 0
    assert oid in result.stdout

def test_S_completion_rejected_with_unresolved():
    oid, d = _create_op("S")
    upd = json.loads((d / "updated-plan.json").read_text())
    sids = [s["step_id"] for s in upd["steps"]]
    now = _now()
    recs = []
    for i, sid in enumerate(sids):
        rec = {"validation_id": f"val-{i:03d}", "covered_step_ids": [sid], "covered_file_paths": [], "method": "command", "command": "pytest", "exit_code": 0, "status": "passed", "captured_output_path": f"evidence/val-{i:03d}.txt", "evidence_paths": [], "file_states": {}, "recorded_at": now, "last_verified_at": now}
        recs.append(rec)
        (d / "evidence").mkdir(exist_ok=True)
        (d / "evidence" / f"val-{i:03d}.txt").write_text("ok", encoding="utf-8")
    cont = update_work_state(operation_id=oid, expected_revision=1, completed_step_ids=sids, current_step_id=None, changed_files=[], created_files=[], deleted_files=[], validation_records=recs, unresolved_items=[{"type": "todo", "description": "unresolved"}], last_completed_action="done", next_exact_action=None)
    assert cont["operation_status"] != "completed"

def test_T_no_conversation():
    oid, d = _create_op("T")
    for fn in ["updated-plan.json", "work-status.json", "validation-report.json", "continuation-package.json"]:
        content = (d / fn).read_text(encoding="utf-8").lower()
        assert "user message" not in content
        assert "assistant message" not in content
        assert "chat history" not in content
        assert "conversation history" not in content
        assert "interaction log" not in content
