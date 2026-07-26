from __future__ import annotations

import json
import sys

from aflow_cli.contracts import ContractError, execute_command, version_fields
from aflow_cli.core import review_plan
from aflow_cli.mcp_server import aflow_status, aflow_verify, status_tool_schema, verify_tool_schema
from aflow_cli.verification import status_operation, verify_operation


def _operation(tmp_path, monkeypatch):
    monkeypatch.setenv("AFLOW_OPERATIONS_ROOT", str(tmp_path / "operations"))
    oid = "verification-test"
    review_plan(
        original_plan="## Implementation\nObjective: verify\nSuccess criteria: evidence\nValidation: command\nFixture: sample\n",
        plan_id=oid,
        operation_id=oid,
        repository_root=str(tmp_path),
        user_prompt="test",
        force_canonical=True,
    )
    from aflow_cli.work_state import _operation_dir
    op = _operation_dir(oid)
    op.joinpath("success-criteria.json").write_text(json.dumps({**version_fields(), "criteria": [{"criterion_id": "criterion-implementation", "source_plan_step_ids": ["step-001"]}]}), encoding="utf-8")
    op.joinpath("validation-specs.json").write_text(json.dumps({"validations": [{**version_fields(), "validation_id": "validation-implementation", "covered_criterion_ids": ["criterion-implementation"], "executable": sys.executable, "arguments": ["-c", "print('ok')"], "working_directory": ".", "timeout_seconds": 3, "environment_allowlist": [], "stdin_policy": "closed", "expected_exit_codes": [0], "maximum_stdout_bytes": 1000, "maximum_stderr_bytes": 1000, "encoding": "utf-8", "required": True}]}), encoding="utf-8")
    op.joinpath("fixture-cases.json").write_text(json.dumps({"fixtures": []}), encoding="utf-8")
    return oid


def test_structured_command_redacts_and_executes(tmp_path):
    spec = {**version_fields(), "validation_id": "redact", "executable": sys.executable, "arguments": ["-c", "print('api_key=secret-value')"], "working_directory": ".", "timeout_seconds": 3, "environment_allowlist": [], "stdin_policy": "closed", "expected_exit_codes": [0], "required": True}
    result = execute_command(spec, tmp_path, tmp_path / "evidence")
    assert result["status"] == "passed"
    assert "secret-value" not in result["stdout"]


def test_structured_command_timeout(tmp_path):
    spec = {**version_fields(), "validation_id": "timeout", "executable": sys.executable, "arguments": ["-c", "import time; time.sleep(1)"], "working_directory": ".", "timeout_seconds": 0.05, "environment_allowlist": [], "stdin_policy": "closed", "expected_exit_codes": [0], "required": True}
    assert execute_command(spec, tmp_path, tmp_path / "evidence")["status"] == "timed_out"


def test_verification_persists_attempt_and_cli_mcp_share_service(tmp_path, monkeypatch):
    oid = _operation(tmp_path, monkeypatch)
    result = verify_operation(oid)
    assert result["verification_status"] == "passed"
    assert result["verification_attempt"] == 1
    from aflow_cli.work_state import _operation_dir
    op = _operation_dir(oid)
    assert (op / "verification" / "attempts" / "000001" / "final-result.json").exists()
    assert aflow_verify(operation_id=oid)["verification_attempt"] == 2
    assert aflow_status(operation_id=oid)["operation_id"] == oid
    assert status_operation(oid)["latest_verification_status"] == "passed"


def test_verification_and_status_mcp_schemas_are_strict():
    assert verify_tool_schema()["inputSchema"]["additionalProperties"] is False
    assert status_tool_schema()["inputSchema"]["additionalProperties"] is False


def test_legacy_validation_artifact_is_rejected(tmp_path):
    spec = {"validation_id": "legacy", "executable": sys.executable, "arguments": [], "working_directory": ".", "timeout_seconds": 1, "expected_exit_codes": [0], "required": True}
    result = execute_command(spec, tmp_path, tmp_path / "evidence")
    assert result["status"] == "unsupported"


def test_command_enforces_closed_stdin_and_output_limits(tmp_path):
    spec = {**version_fields(), "validation_id": "limits", "executable": sys.executable, "arguments": ["-c", "print('0123456789')"], "working_directory": ".", "timeout_seconds": 3, "environment_allowlist": [], "stdin_policy": "closed", "expected_exit_codes": [0], "maximum_stdout_bytes": 4, "maximum_stderr_bytes": 4, "required": True}
    result = execute_command(spec, tmp_path, tmp_path / "evidence")
    assert result["status"] == "passed"
    assert result["stdout"].endswith("[output truncated]")
    assert result["output_limits"] == {"stdout_bytes": 4, "stderr_bytes": 4}

    invalid = {**spec, "validation_id": "open-stdin", "stdin_policy": "inherit"}
    assert execute_command(invalid, tmp_path, tmp_path / "evidence")["status"] == "unsupported"
