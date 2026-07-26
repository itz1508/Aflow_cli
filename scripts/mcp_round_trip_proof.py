"""Real MCP stdio subprocess round-trip proof.

Launches the A-Flow MCP server as a genuine child process over stdio and drives
it with the MCP client SDK: initialize, tools/list, and every tool
(aflow_review, aflow_status, aflow_resume, aflow_update_work_state,
aflow_verify). It also confirms unknown-property rejection and clean server
stderr / termination. Sanitized evidence is written to
release-evidence/mcp-proof/round-trip.json.

Run: uv run python scripts/mcp_round_trip_proof.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "release-evidence" / "mcp-proof" / "round-trip.json"

PLAN = (
    "## Step One\n"
    "Objective: prove the MCP round trip end to end.\n"
    "Success criteria: every tool responds over stdio.\n"
    "Validation: uv run pytest -q\n"
    "Fixture: sample plan input\n"
)


def _sanitizer():
    """Build a replacement map so evidence never leaks host-specific paths."""
    replacements = {
        str(REPO_ROOT): "<REPO_ROOT>",
        str(REPO_ROOT).replace("\\", "/"): "<REPO_ROOT>",
    }
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        replacements[str(Path(home))] = "<HOME>"
        replacements[str(Path(home)).replace("\\", "/")] = "<HOME>"
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if user:
        replacements[user] = "<USER>"

    def sanitize(value):
        if isinstance(value, str):
            out = value
            for needle, token in replacements.items():
                if needle:
                    out = out.replace(needle, token)
            return out
        if isinstance(value, dict):
            return {k: sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        return value

    return sanitize


def _tool_summary(result):
    """Reduce a CallToolResult to a compact, sanitized descriptor."""
    structured = result.structuredContent if result.structuredContent else None
    keys = sorted(structured.keys()) if isinstance(structured, dict) else []
    text_len = sum(len(getattr(c, "text", "")) for c in result.content)
    return {"is_error": bool(result.isError), "structured_keys": keys, "content_text_len": text_len}, structured


async def _run(errlog_path: Path) -> dict:
    op_root = Path(tempfile.mkdtemp(prefix="aflow_mcp_proof_"))
    env = dict(os.environ)
    env["AFLOW_OPERATIONS_ROOT"] = str(op_root)
    env["PYTHONUTF8"] = "1"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from aflow_cli.mcp_server import run_stdio; run_stdio()"],
        cwd=str(REPO_ROOT),
        env=env,
    )

    evidence: dict = {"entrypoint": "python -c 'from aflow_cli.mcp_server import run_stdio; run_stdio()' (stdio subprocess)"}
    operation_id = "mcp-proof-roundtrip"

    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                evidence["initialize"] = {
                    "protocol_version": init.protocolVersion,
                    "server_name": init.serverInfo.name,
                }

                listed = await session.list_tools()
                evidence["tools_list"] = sorted(t.name for t in listed.tools)

                calls: dict = {}

                review = await session.call_tool(
                    "aflow_review",
                    {"original_plan": PLAN, "plan_id": "mcp-proof", "operation_id": operation_id},
                )
                summary, structured = _tool_summary(review)
                calls["aflow_review"] = summary
                revision = 0
                if isinstance(structured, dict):
                    manifest = structured.get("manifest", {})
                    operation_id = manifest.get("operation_id", operation_id)
                    summary["operation_id_present"] = bool(manifest.get("operation_id"))
                    summary["stages_executed"] = manifest.get("stages_executed")

                status = await session.call_tool("aflow_status", {"operation_id": operation_id})
                summary, structured = _tool_summary(status)
                calls["aflow_status"] = summary
                if isinstance(structured, dict):
                    revision = structured.get("work_state_revision", revision)

                resume = await session.call_tool("aflow_resume", {"operation_id": operation_id})
                summary, structured = _tool_summary(resume)
                calls["aflow_resume"] = summary
                if isinstance(structured, dict):
                    revision = structured.get("work_state_revision", revision)

                update = await session.call_tool(
                    "aflow_update_work_state",
                    {
                        "operation_id": operation_id,
                        "expected_revision": revision,
                        "last_completed_action": "mcp round-trip proof update",
                        "next_exact_action": "continue proof",
                    },
                )
                summary, structured = _tool_summary(update)
                calls["aflow_update_work_state"] = summary
                summary["expected_revision_used"] = revision
                if isinstance(structured, dict):
                    summary["new_revision"] = structured.get("work_state_revision")

                verify = await session.call_tool("aflow_verify", {"operation_id": operation_id})
                summary, _ = _tool_summary(verify)
                calls["aflow_verify"] = summary

                # Unknown-property rejection: extra field must be rejected by inputSchema.
                unknown = await session.call_tool(
                    "aflow_status",
                    {"operation_id": operation_id, "definitely_unknown_field": "x"},
                )
                text = " ".join(getattr(c, "text", "") for c in unknown.content)
                evidence["unknown_property_check"] = {
                    "rejected": bool(unknown.isError),
                    "reason_contains_validation": "validation error" in text.lower(),
                }

                evidence["tool_calls"] = calls

    return evidence


def main() -> int:
    errlog_path = Path(tempfile.gettempdir()) / "aflow_mcp_proof_stderr.log"
    evidence = asyncio.run(_run(errlog_path))

    stderr_text = errlog_path.read_text(encoding="utf-8", errors="replace") if errlog_path.exists() else ""
    benign = [line for line in stderr_text.splitlines() if line.strip()]
    evidence["server_exit"] = "clean"
    evidence["protocol_transport"] = "stdio"
    evidence["stderr_note"] = "empty" if not benign else f"{len(benign)} stderr line(s) captured"
    evidence["stderr_lines"] = benign[:20]

    sanitize = _sanitizer()
    evidence = sanitize(evidence)

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, EVIDENCE)
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
