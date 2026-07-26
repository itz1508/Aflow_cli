from __future__ import annotations

import jsonschema
import pytest

from aflow.mcp_server import (
    aflow_review,
    resume_tool_schema,
    status_tool_schema,
    tool_schema,
    update_work_state_tool_schema,
    verify_tool_schema,
)


def test_mcp_tool_has_one_strict_plan_only_contract():
    schema = tool_schema()
    assert schema["name"] == "aflow_review"
    assert schema["inputSchema"]["additionalProperties"] is False
    assert set(schema["inputSchema"]["required"]) == {"original_plan", "plan_id"}
    # Required fields must be exactly plan-only
    assert "original_plan" in schema["inputSchema"]["properties"]
    assert "plan_id" in schema["inputSchema"]["properties"]
    # Optional canonical fields are allowed for operation reuse and evidence
    allowed_optional = {"plan_digest", "operation_id", "repository_root", "user_prompt"}
    for prop in schema["inputSchema"]["properties"]:
        if prop not in {"original_plan", "plan_id"}:
            assert prop in allowed_optional, f"unexpected property {prop} not in allowed optional set"


def test_mcp_review_returns_a_plan_only_additive_bundle():
    plan = "Objective: review\nValidation: test\nFixture: sample\nSuccess criteria: unchanged\n"
    result = aflow_review(original_plan=plan, plan_id="mcp")
    assert result["manifest"]["plan_id"] == "mcp"
    assert "manifest.json" in result["artifacts"]
    # Canonical workflow properties
    assert result["manifest"].get("stages_executed") == 11
    assert result["manifest"].get("operation_id")
    assert "original-plan-digest.json" in result["artifacts"] or "intake.json" in result["artifacts"]


@pytest.mark.parametrize(
    "schema_factory, valid_args",
    [
        (tool_schema, {"original_plan": "x", "plan_id": "p"}),
        (update_work_state_tool_schema, {"operation_id": "o", "expected_revision": 1}),
        (resume_tool_schema, {"operation_id": "o"}),
        (verify_tool_schema, {"operation_id": "o"}),
        (status_tool_schema, {"operation_id": "o"}),
    ],
)
def test_every_mcp_tool_schema_rejects_unknown_properties(schema_factory, valid_args):
    # The low-level MCP server validates arguments against inputSchema via
    # jsonschema before dispatch; additionalProperties:False makes any unknown
    # field a hard rejection. This locks that contract for every tool.
    schema = schema_factory()["inputSchema"]
    assert schema["additionalProperties"] is False
    jsonschema.validate(instance=valid_args, schema=schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={**valid_args, "definitely_unknown_field": "x"}, schema=schema)
