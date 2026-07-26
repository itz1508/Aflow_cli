from __future__ import annotations

import datetime
import json
import sys

from aflow.contracts import version_fields
from aflow.core import review_plan
from aflow.verification import verify_operation
from aflow.work_state import _operation_dir, load_continuation_package, update_work_state


def _passing_operation(tmp_path, monkeypatch):
    """Create an operation whose single validation passes and covers step-001."""
    monkeypatch.setenv("AFLOW_OPERATIONS_ROOT", str(tmp_path / "operations"))
    oid = "transaction-proof"
    review_plan(
        original_plan="## Implementation\nObjective: verify\nSuccess criteria: evidence\nValidation: command\nFixture: sample\n",
        plan_id=oid,
        operation_id=oid,
        repository_root=str(tmp_path),
        user_prompt="test",
        force_canonical=True,
    )
    op = _operation_dir(oid)
    op.joinpath("success-criteria.json").write_text(
        json.dumps({**version_fields(), "criteria": [{"criterion_id": "criterion-implementation", "source_plan_step_ids": ["step-001"]}]}),
        encoding="utf-8",
    )
    op.joinpath("validation-specs.json").write_text(
        json.dumps({"validations": [{
            **version_fields(),
            "validation_id": "validation-implementation",
            "covered_criterion_ids": ["criterion-implementation"],
            "covered_step_ids": ["step-001"],
            "executable": sys.executable,
            "arguments": ["-c", "print('ok')"],
            "working_directory": ".",
            "timeout_seconds": 5,
            "environment_allowlist": [],
            "stdin_policy": "closed",
            "expected_exit_codes": [0],
            "maximum_stdout_bytes": 1000,
            "maximum_stderr_bytes": 1000,
            "encoding": "utf-8",
            "required": True,
        }]}),
        encoding="utf-8",
    )
    op.joinpath("fixture-cases.json").write_text(json.dumps({"fixtures": []}), encoding="utf-8")
    return oid, op


def test_successful_verification_bumps_revision_exactly_once(tmp_path, monkeypatch):
    oid, op = _passing_operation(tmp_path, monkeypatch)
    rev_before = load_continuation_package(oid)["work_state_revision"]

    first = verify_operation(oid)
    rev_after_first = load_continuation_package(oid)["work_state_revision"]
    assert first["verification_status"] == "passed"
    assert first["verification_attempt"] == 1
    # A successful verification that completes steps writes exactly one new revision.
    assert rev_after_first == rev_before + 1

    second = verify_operation(oid)
    rev_after_second = load_continuation_package(oid)["work_state_revision"]
    assert second["verification_attempt"] == 2
    assert rev_after_second == rev_after_first + 1


def test_verification_attempts_are_historical(tmp_path, monkeypatch):
    oid, op = _passing_operation(tmp_path, monkeypatch)
    verify_operation(oid)
    verify_operation(oid)
    verify_operation(oid)
    attempts = op / "verification" / "attempts"
    kept = sorted(p.name for p in attempts.iterdir() if p.is_dir())
    # Every attempt is preserved with a monotonically increasing zero-padded id.
    assert kept == ["000001", "000002", "000003"]
    for name in kept:
        assert (attempts / name / "final-result.json").exists()


def test_interrupted_verification_write_leaves_previous_result_intact(tmp_path, monkeypatch):
    oid, op = _passing_operation(tmp_path, monkeypatch)
    verify_operation(oid)
    good = json.loads((op / "final-result.json").read_text(encoding="utf-8"))
    # Simulate an interrupted atomic write: a stray .tmp must never be promoted.
    (op / "final-result.json.tmp").write_text("{ half-written", encoding="utf-8")
    reloaded = json.loads((op / "final-result.json").read_text(encoding="utf-8"))
    assert reloaded["verification_attempt"] == good["verification_attempt"]
    # A subsequent verification still succeeds via os.replace and supersedes cleanly.
    verify_operation(oid)
    assert json.loads((op / "final-result.json").read_text(encoding="utf-8"))["verification_attempt"] == 2


def test_unresolved_items_block_completion_and_steer_next_action(tmp_path, monkeypatch):
    """When every step is complete but unresolved observations remain, the
    operation must stay in_progress and next_exact_action must point at the
    unresolved items rather than at non-existent remaining work."""
    oid, op = _passing_operation(tmp_path, monkeypatch)
    sid = json.loads((op / "updated-plan.json").read_text(encoding="utf-8"))["steps"][0]["step_id"]
    rec = {
        "validation_id": "val-implementation",
        "covered_step_ids": [sid],
        "covered_file_paths": [],
        "method": "command",
        "command": "python -c pass",
        "exit_code": 0,
        "status": "passed",
        "captured_output_path": "evidence/x.txt",
        "evidence_paths": [],
        "file_states": {},
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "last_verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    cont = update_work_state(
        operation_id=oid,
        expected_revision=1,
        completed_step_ids=[sid],
        current_step_id=None,
        changed_files=[],
        created_files=[],
        deleted_files=[],
        validation_records=[rec],
        unresolved_items=[{"type": "semantic_observation", "description": "advisory only", "resolution_status": "unresolved"}],
        last_completed_action="done",
        next_exact_action=None,
    )
    assert cont["operation_status"] == "in_progress"
    assert cont["unresolved_items"]
    assert cont["next_exact_action"] == "Resolve unresolved items"
