from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest


def test_trigger_schema_keeps_tasks_and_plans_out_of_the_same_input_path() -> None:
    schema_path = Path(__file__).parents[1] / "schemas" / "v1" / "aflow-trigger-context.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["source_kind"]["enum"]) == {"task", "plan"}
    actions = {rule.get("then", {}).get("properties", {}).get("trigger_action", {}).get("const") for rule in schema["allOf"]}
    assert {"direct_review", "draft_plan", "skip"} <= actions

    jsonschema.validate(
        {"request_id": "plan", "source_kind": "plan", "work_kind": "implementation", "read_only": False, "expects_mutation": True, "original_plan": "plan", "trigger_action": "direct_review"},
        schema,
    )
    jsonschema.validate(
        {"request_id": "task", "source_kind": "task", "work_kind": "fix", "read_only": False, "expects_mutation": True, "task_summary": "fix it", "trigger_action": "draft_plan"},
        schema,
    )
    jsonschema.validate(
        {"request_id": "read-only", "source_kind": "task", "work_kind": "research", "read_only": True, "expects_mutation": False, "task_summary": "research", "trigger_action": "skip"},
        schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"request_id": "invalid", "source_kind": "task", "work_kind": "fix", "read_only": False, "expects_mutation": True, "task_summary": "fix it", "trigger_action": "direct_review"},
            schema,
        )
