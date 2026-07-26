"""Stage 1: Canonical intake creation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class CanonicalIntake:
    """Normalized intake for the A-Flow workflow."""
    operation_id: str
    plan_id: str
    repository_root: str | None
    user_prompt: str
    original_plan: str
    agent_or_host: Literal["agent", "host"]
    execution_kind: Literal["implementation", "configuration", "fix", "analysis", "research", "question"]
    repository_context: dict[str, Any]
    intake_metadata: dict[str, Any] = field(default_factory=dict)

    def compute_digest(self) -> str:
        """Compute SHA-256 digest of original_plan."""
        return hashlib.sha256(self.original_plan.encode("utf-8")).hexdigest()

    def to_mapping(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "operation_id": self.operation_id,
            "plan_id": self.plan_id,
            "repository_root": self.repository_root,
            "user_prompt": self.user_prompt,
            "original_plan": self.original_plan,
            "agent_or_host": self.agent_or_host,
            "execution_kind": self.execution_kind,
            "repository_context": self.repository_context,
            "intake_metadata": self.intake_metadata,
            "plan_digest": self.compute_digest(),
        }


def create_intake(
    *,
    operation_id: str | None = None,
    plan_id: str = "",
    repository_root: str | None = None,
    user_prompt: str = "",
    original_plan: str = "",
    agent_or_host: Literal["agent", "host"] = "agent",
    execution_kind: Literal["implementation", "configuration", "fix", "analysis", "research", "question"] = "implementation",
    repository_context: dict[str, Any] | None = None,
) -> CanonicalIntake:
    """Create canonical intake from raw inputs.

    If operation_id is None, generates one from plan_id.
    """
    if not plan_id.strip():
        raise ValueError("plan_id must be non-empty")
    if not original_plan.strip():
        raise ValueError("original_plan must be non-empty")

    if operation_id is None:
        operation_id = f"aflow-{plan_id}"

    return CanonicalIntake(
        operation_id=operation_id,
        plan_id=plan_id,
        repository_root=repository_root,
        user_prompt=user_prompt,
        original_plan=original_plan,
        agent_or_host=agent_or_host,
        execution_kind=execution_kind,
        repository_context=repository_context or {},
        intake_metadata={"source": "aflow"},
    )