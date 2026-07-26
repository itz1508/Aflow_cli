from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import ReviewError, review_plan
from .work_state import resume_operation, update_work_state
from .verification import status_operation, verify_operation

def tool_schema():
    return {
        "name": "aflow_review",
        "description": "Review an immutable plan and return additive companion artifacts. Never edits the plan or repository.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "original_plan": {"type": "string"},
                "plan_id": {"type": "string"},
                "plan_digest": {"type": "string"},
                "operation_id": {"type": "string"},
                "repository_root": {"type": "string"},
                "user_prompt": {"type": "string"},
            },
            "required": ["original_plan", "plan_id"],
        },
    }

def update_work_state_tool_schema():
    return {
        "name": "aflow_update_work_state",
        "description": "Update work-state continuity after validated engineering work.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "operation_id": {"type": "string"},
                "expected_revision": {"type": "integer"},
                "completed_step_ids": {"type": "array", "items": {"type": "string"}},
                "current_step_id": {"type": ["string", "null"]},
                "changed_files": {"type": "array", "items": {"type": "string"}},
                "created_files": {"type": "array", "items": {"type": "string"}},
                "deleted_files": {"type": "array", "items": {"type": "string"}},
                "validation_records": {"type": "array"},
                "unresolved_items": {"type": "array"},
                "last_completed_action": {"type": ["string", "null"]},
                "next_exact_action": {"type": ["string", "null"]},
            },
            "required": ["operation_id", "expected_revision"],
        },
    }

def resume_tool_schema():
    return {
        "name": "aflow_resume",
        "description": "Resume operation from latest persisted work-state using operation_id only.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "operation_id": {"type": "string"},
            },
            "required": ["operation_id"],
        },
    }

def verify_tool_schema():
    return {"name": "aflow_verify", "description": "Independently execute persisted validations and fixtures.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"operation_id": {"type": "string"}}, "required": ["operation_id"]}}

def status_tool_schema():
    return {"name": "aflow_status", "description": "Read current operation engineering state without mutation.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"operation_id": {"type": "string"}}, "required": ["operation_id"]}}

def _normalize_operation_id(operation_id, plan_id):
    if operation_id and operation_id.strip():
        return operation_id.strip()
    return f"aflow-{plan_id}"

def aflow_review(*, original_plan, plan_id, plan_digest=None, operation_id=None, repository_root=None, user_prompt="", repository_context=None):
    op_id = _normalize_operation_id(operation_id, plan_id)
    repo_root = repository_root or str(Path.cwd())
    if not user_prompt:
        user_prompt = f"MCP review of plan_id={plan_id}"
    
    # Call semantic with operation context for provenance
    from .semantic import local_semantic_notes
    from .stages.operation_setup import setup_operation
    operation = setup_operation(operation_id=op_id, plan_id=plan_id, repository_root=repo_root)
    notes, _ = local_semantic_notes(original_plan, operation_id=op_id, operation_dir_path=operation.operation_dir)
    
    return review_plan(original_plan=original_plan, plan_id=plan_id, plan_digest=plan_digest, operation_id=op_id, repository_root=repo_root, user_prompt=user_prompt, repository_context=repository_context or {"source": "mcp"}, semantic_notes=notes, force_canonical=True, original_bytes=original_plan.encode("utf-8"))

def aflow_update_work_state(*, operation_id, expected_revision, completed_step_ids=None, current_step_id=None, changed_files=None, created_files=None, deleted_files=None, validation_records=None, unresolved_items=None, last_completed_action=None, next_exact_action=None):
    return update_work_state(operation_id=operation_id, expected_revision=expected_revision, completed_step_ids=completed_step_ids or [], current_step_id=current_step_id, changed_files=changed_files or [], created_files=created_files or [], deleted_files=deleted_files or [], validation_records=validation_records or [], unresolved_items=unresolved_items or [], last_completed_action=last_completed_action, next_exact_action=next_exact_action)

def aflow_resume(*, operation_id):
    return resume_operation(operation_id=operation_id)

def aflow_verify(*, operation_id):
    return verify_operation(operation_id=operation_id)

def aflow_status(*, operation_id):
    return status_operation(operation_id=operation_id)

_TOOL_SCHEMAS = (
    tool_schema,
    update_work_state_tool_schema,
    resume_tool_schema,
    verify_tool_schema,
    status_tool_schema,
)

_TOOL_DISPATCH = {
    "aflow_review": aflow_review,
    "aflow_update_work_state": aflow_update_work_state,
    "aflow_resume": aflow_resume,
    "aflow_verify": aflow_verify,
    "aflow_status": aflow_status,
}

def run_stdio():
    # Use the low-level Server so tool arguments are validated against each
    # inputSchema (additionalProperties: False) via jsonschema before dispatch.
    # FastMCP silently drops unknown properties (pydantic extra="ignore"), which
    # would let malformed calls through; the low-level path rejects them.
    import anyio
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server = Server("A-Flow")
    tools = [
        types.Tool(name=s["name"], description=s["description"], inputSchema=s["inputSchema"])
        for s in (factory() for factory in _TOOL_SCHEMAS)
    ]

    @server.list_tools()
    async def _list_tools():
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]):
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            raise ReviewError(f"unknown tool: {name}")
        result = fn(**(arguments or {}))
        if not isinstance(result, dict):
            return {"result": result}
        return result

    async def _main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_main)

