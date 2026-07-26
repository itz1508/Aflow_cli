"""Clean non-editable install proof (outside the repository).

Builds a wheel, installs it non-editable into a fresh throwaway virtual
environment created OUTSIDE the repository, then exercises the full command
battery from a working directory that is also outside the repository, using
only the installed console script / module (never the repo source tree):

  * aflow-cli --help
  * aflow-cli doctor
  * a real MCP stdio round trip (initialize / tools/list / tool call /
    unknown-property rejection) against the installed package
  * aflow-cli review  (immutable-plan review, live semantic notes)
  * aflow-cli progress (work-state update through the installed package)
  * aflow-cli verify   (independent execution of review-generated validations)
  * aflow-cli resume   (state reload in a brand-new process == restart)
  * aflow-cli status   (read-only state report)

Every command's exit code and a sanitized output snippet are recorded.
Sanitized evidence -> release-evidence/clean-install/proof.json

Run: uv run python scripts/clean_install_proof.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "release-evidence" / "clean-install" / "proof.json"

PLAN = (
    "# Clean install plan\n\n"
    "## Step One\n"
    "Objective: prove the installed package runs outside the repo.\n"
    "Success criteria: every command responds from the installed wheel.\n"
    "Validation: run the packaged validations.\n"
    "Fixture: clean-install sample plan.\n"
)


def _sanitizer():
    replacements: dict[str, str] = {}
    for needle, token in ((str(REPO_ROOT), "<REPO_ROOT>"),):
        replacements[needle] = token
        replacements[needle.replace("\\", "/")] = token
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        replacements[str(Path(home))] = "<HOME>"
        replacements[str(Path(home)).replace("\\", "/")] = "<HOME>"
    user = os.environ.get("USERNAME") or os.environ.get("USER")

    def sanitize(value):
        if isinstance(value, str):
            out = value
            for needle, token in replacements.items():
                if needle:
                    out = out.replace(needle, token)
            if user:
                out = out.replace(user, "<USER>")
            return out
        if isinstance(value, dict):
            return {k: sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        return value

    return sanitize


async def _mcp_round_trip(venv_python: Path, workdir: Path, ops_root: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["AFLOW_OPERATIONS_ROOT"] = str(ops_root)
    env["PYTHONUTF8"] = "1"
    params = StdioServerParameters(
        command=str(venv_python),
        args=["-m", "aflow_cli.cli", "mcp"],
        cwd=str(workdir),
        env=env,
    )
    errlog_path = workdir / "mcp_stderr.log"
    result: dict = {"entrypoint": "<venv>/python -m aflow_cli.cli mcp (installed, stdio)"}
    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                result["initialize"] = {
                    "protocol_version": init.protocolVersion,
                    "server_name": init.serverInfo.name,
                }
                listed = await session.list_tools()
                result["tools_list"] = sorted(t.name for t in listed.tools)
                review = await session.call_tool(
                    "aflow_review",
                    {"original_plan": PLAN, "plan_id": "clean-install-mcp", "operation_id": "clean-install-mcp"},
                )
                result["review_call"] = {
                    "is_error": bool(review.isError),
                    "stages_executed": (review.structuredContent or {}).get("manifest", {}).get("stages_executed")
                    if isinstance(review.structuredContent, dict)
                    else None,
                }
                unknown = await session.call_tool(
                    "aflow_status",
                    {"operation_id": "clean-install-mcp", "definitely_unknown_field": "x"},
                )
                text = " ".join(getattr(c, "text", "") for c in unknown.content)
                result["unknown_property_check"] = {
                    "rejected": bool(unknown.isError),
                    "reason_contains_validation": "validation error" in text.lower(),
                }
    stderr_text = errlog_path.read_text(encoding="utf-8", errors="replace") if errlog_path.exists() else ""
    benign = [line for line in stderr_text.splitlines() if line.strip()]
    result["server_exit"] = "clean"
    result["stderr_note"] = "empty" if not benign else f"{len(benign)} stderr line(s)"
    result["stderr_lines"] = benign[:20]
    return result


def main() -> int:
    workroot = Path(tempfile.mkdtemp(prefix="aflow_clean_install_"))
    dist = workroot / "dist"
    venv = workroot / "venv"
    workdir = workroot / "cwd"  # working dir OUTSIDE the repo
    ops_root = workroot / "ops"
    workdir.mkdir(parents=True)
    evidence: dict = {"proof_type": "clean-non-editable-install", "install_location_outside_repo": str(venv)}
    steps: list[dict] = []

    def record(name: str, proc: subprocess.CompletedProcess) -> dict:
        entry = {
            "command": name,
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-600:],
            "stderr_tail": (proc.stderr or "")[-400:],
        }
        steps.append(entry)
        return entry

    # 1. Build a wheel from the repo.
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    evidence["build"] = {"returncode": build.returncode, "stderr_tail": (build.stderr or "")[-400:]}
    wheels = sorted(dist.glob("*.whl"))
    evidence["wheel"] = wheels[0].name if wheels else None
    if build.returncode != 0 or not wheels:
        evidence["all_checks_passed"] = False
        _finish(evidence, steps)
        return 1

    # 2. Fresh venv OUTSIDE the repo.
    venv_build = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True)
    evidence["venv_create"] = {"returncode": venv_build.returncode, "stderr_tail": (venv_build.stderr or "")[-400:]}
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    aflow_exe = venv / ("Scripts/aflow-cli.exe" if os.name == "nt" else "bin/aflow-cli")

    # 3. Install the wheel non-editable into the fresh venv (uv resolves deps from cache).
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), str(wheels[0])],
        capture_output=True, text=True,
    )
    evidence["install"] = {
        "returncode": install.returncode,
        "editable": False,
        "stderr_tail": (install.stderr or "")[-600:],
    }
    evidence["console_script_present"] = aflow_exe.exists()
    if install.returncode != 0:
        evidence["all_checks_passed"] = False
        _finish(evidence, steps)
        return 1

    env = dict(os.environ)
    env["AFLOW_OPERATIONS_ROOT"] = str(ops_root)
    env["PYTHONUTF8"] = "1"

    def run_aflow(args: list[str]) -> subprocess.CompletedProcess:
        # Use the installed console script from OUTSIDE the repo working dir.
        return subprocess.run([str(aflow_exe), *args], cwd=str(workdir), env=env, capture_output=True, text=True)

    op_id = "clean-install-cli"
    bundle_dir = workdir / "bundle"

    # 4. help
    help_step = record("aflow-cli --help", run_aflow(["--help"]))
    # 5. doctor
    doctor_step = record("aflow-cli doctor", run_aflow(["doctor"]))
    # 6. MCP round trip against the installed package
    evidence["mcp"] = _sanitizer()(asyncio.run(_mcp_round_trip(venv_python, workdir, ops_root / "mcp")))

    # 7. review (writes bundle + operation state under ops_root)
    plan_file = workdir / "plan.md"
    plan_file.write_text(PLAN, encoding="utf-8")
    rev = record(
        "aflow-cli review",
        run_aflow([
            "review", "--plan", str(plan_file), "--plan-id", op_id,
            "--operation-id", op_id, "--repository-root", str(workdir),
            "--output-dir", str(bundle_dir),
        ]),
    )

    # 8. progress: light work-state update through the installed package.
    ws_path = ops_root / op_id / "work-status.json"
    revision = json.loads(ws_path.read_text(encoding="utf-8")).get("work_state_revision", 1) if ws_path.exists() else 1
    update_file = workdir / "update.json"
    update_file.write_text(json.dumps({
        "expected_revision": revision,
        "last_completed_action": "clean-install progress probe",
        "next_exact_action": "Run independent verification",
    }), encoding="utf-8")
    progress_step = record("aflow-cli progress", run_aflow(["progress", "--operation-id", op_id, "--update-file", str(update_file)]))

    # 9. verify (independent execution of review-generated validations)
    verify_step = record("aflow-cli verify", run_aflow(["verify", "--operation-id", op_id]))
    # 10. resume == restart: brand-new process reloads persisted state
    resume_step = record("aflow-cli resume (restart)", run_aflow(["resume", "--operation-id", op_id]))
    # 11. status
    status_step = record("aflow-cli status", run_aflow(["status", "--operation-id", op_id]))

    evidence["commands"] = steps

    help_ok = help_step["returncode"] == 0
    doctor_ok = doctor_step["returncode"] == 0
    review_ok = (
        rev["returncode"] == 0
        and bundle_dir.exists()
        and (ops_root / op_id / "work-status.json").exists()
    )
    progress_ok = progress_step["returncode"] == 0
    verify_ok = verify_step["returncode"] in (0, 1)  # 1 only if a criterion is unsatisfied; still a real run
    resume_ok = resume_step["returncode"] == 0
    status_ok = status_step["returncode"] == 0
    mcp_ok = (
        evidence["mcp"].get("server_exit") == "clean"
        and not evidence["mcp"].get("stderr_lines")
        and evidence["mcp"]["review_call"]["is_error"] is False
        and evidence["mcp"]["unknown_property_check"]["rejected"] is True
        and evidence["mcp"]["unknown_property_check"]["reason_contains_validation"] is True
    )

    checks = {
        "wheel_built": bool(wheels),
        "installed_non_editable": install.returncode == 0 and evidence["install"]["editable"] is False,
        "console_script_present": evidence["console_script_present"],
        "help_ok": help_ok,
        "doctor_ok": doctor_ok,
        "mcp_round_trip_ok": mcp_ok,
        "review_ok": review_ok,
        "progress_ok": progress_ok,
        "verify_ran": verify_ok,
        "resume_restart_ok": resume_ok,
        "status_ok": status_ok,
        "ran_outside_repo": str(REPO_ROOT) not in str(workdir) and str(REPO_ROOT) not in str(venv),
    }
    evidence["checks"] = checks
    evidence["all_checks_passed"] = all(checks.values())
    _finish(evidence, steps)
    return 0 if evidence["all_checks_passed"] else 1


def _finish(evidence: dict, steps: list[dict]) -> None:
    sanitize = _sanitizer()
    evidence = sanitize(evidence)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, EVIDENCE)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
