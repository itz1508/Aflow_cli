"""Versioned executable artifact and safe process contracts."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ARTIFACT_VERSION = "1.0"
PRODUCER_VERSION = "0.1.0"
MAX_OUTPUT = 64 * 1024
SECRET_RE = re.compile(r"(?i)(token|api[-_]?key|authorization|cookie|password|credential)(\s*[:=]\s*)([^\s,;]+)")


class ContractError(ValueError):
    pass


def version_fields(*, artifact_version: str = ARTIFACT_VERSION) -> dict[str, str]:
    return {"schema_version": "1.0.0", "artifact_version": artifact_version, "producer_version": PRODUCER_VERSION}


def validate_versioned(value: dict[str, Any], kind: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{kind} must be an object")
    for key in ("schema_version", "artifact_version", "producer_version"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ContractError(f"{kind} missing {key}")
    if value["artifact_version"] != ARTIFACT_VERSION:
        raise ContractError(f"unsupported {kind} artifact_version: {value['artifact_version']}")


def redact(value: str, maximum: int = MAX_OUTPUT) -> str:
    clipped = value[:maximum]
    clipped = SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", clipped)
    return clipped + ("\n[output truncated]" if len(value) > maximum else "")


def canonical_child(root: Path, candidate: str | None) -> Path:
    if not candidate:
        return root.resolve()
    raw = Path(candidate)
    target = (root / raw if not raw.is_absolute() else raw).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ContractError(f"path escapes repository root: {candidate}") from exc
    return target


def validate_command(spec: dict[str, Any], root: Path) -> Path:
    validate_versioned(spec, "validation")
    required = ("validation_id", "executable", "arguments", "working_directory", "timeout_seconds", "expected_exit_codes", "required")
    for key in required:
        if key not in spec:
            raise ContractError(f"validation missing {key}")
    if not isinstance(spec["arguments"], list) or not all(isinstance(x, str) for x in spec["arguments"]):
        raise ContractError("validation arguments must be a string array")
    if not isinstance(spec["expected_exit_codes"], list) or not all(isinstance(x, int) for x in spec["expected_exit_codes"]):
        raise ContractError("expected_exit_codes must be an integer array")
    timeout = spec["timeout_seconds"]
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 3600:
        raise ContractError("timeout_seconds must be between 0 and 3600")
    if not isinstance(spec["required"], bool):
        raise ContractError("required must be boolean")
    if spec.get("stdin_policy", "closed") != "closed":
        raise ContractError("stdin_policy must be closed")
    for field in ("maximum_stdout_bytes", "maximum_stderr_bytes"):
        limit = spec.get(field, MAX_OUTPUT)
        if not isinstance(limit, int) or limit < 1 or limit > MAX_OUTPUT:
            raise ContractError(f"{field} must be an integer between 1 and {MAX_OUTPUT}")
    return canonical_child(root, spec["working_directory"])


def execute_command(spec: dict[str, Any], root: Path, evidence_dir: Path) -> dict[str, Any]:
    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    try:
        cwd = validate_command(spec, root)
    except ContractError as exc:
        return {"status": "unsupported", "error": str(exc), "started_at": started_at, "completed_at": started_at, "duration_seconds": 0.0}
    executable = spec["executable"]
    if not isinstance(executable, str) or not executable.strip() or executable.lower() in {"cmd", "powershell", "pwsh", "bash", "sh", "ssh"}:
        return {"status": "unsupported", "error": "interactive or shell executable is not allowed", "started_at": started_at, "completed_at": started_at, "duration_seconds": 0.0}
    env = None
    allow = spec.get("environment_allowlist", [])
    if allow is not None:
        if not isinstance(allow, list) or not all(isinstance(x, str) for x in allow):
            return {"status": "unsupported", "error": "environment_allowlist must be a string array", "started_at": started_at, "completed_at": started_at, "duration_seconds": 0.0}
        env = {key: os.environ[key] for key in allow if key in os.environ}
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run([executable, *spec["arguments"]], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding=spec.get("encoding", "utf-8"), timeout=float(spec["timeout_seconds"]), shell=False)
        status = "passed" if proc.returncode in spec["expected_exit_codes"] else "failed"
        error = None
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        status, error, exit_code = "timed_out", "command timed out", None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    except (OSError, UnicodeError) as exc:
        status, error, exit_code = "unsupported", str(exc), None
        stdout = stderr = ""
    else:
        stdout, stderr = proc.stdout or "", proc.stderr or ""
    completed = time.time()
    stdout_limit = int(spec.get("maximum_stdout_bytes", MAX_OUTPUT))
    stderr_limit = int(spec.get("maximum_stderr_bytes", MAX_OUTPUT))
    result = {"validation_id": spec["validation_id"], "status": status, "exit_code": exit_code, "command": [executable, *spec["arguments"]], "working_directory": str(cwd), "started_at": started_at, "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(completed)), "duration_seconds": round(completed - started, 6), "stdout": redact(stdout, stdout_limit), "stderr": redact(stderr, stderr_limit), "output_limits": {"stdout_bytes": stdout_limit, "stderr_bytes": stderr_limit}}
    if error:
        result["error"] = redact(error)
    evidence = evidence_dir / f"{spec['validation_id']}.json"
    evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["evidence_paths"] = [str(evidence)]
    return result
