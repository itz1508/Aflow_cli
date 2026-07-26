"""Stage 2: Operation setup and directory creation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OperationSetup:
    """Operation directory and state tracking."""
    operation_id: str
    operation_dir: Path
    operation_config: dict[str, Any]
    stage_state: dict[str, Any] = field(default_factory=dict)
    already_existed: bool = False

    def persist_config(self) -> None:
        """Write operation config to disk."""
        config_path = self.operation_dir / "operation-config.json"
        content = json.dumps(self.operation_config, sort_keys=True, indent=2) + "\n"
        temp = config_path.with_suffix(".tmp")
        temp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temp, config_path)


def _get_operations_root() -> Path:
    """Get the root directory for operations."""
    configured = os.environ.get("AFLOW_OPERATIONS_ROOT")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "AFlow"
    else:
        base = Path.home() / ".aflow"
    return base / "operations"


def setup_operation(*, operation_id: str, plan_id: str, repository_root: str | None = None) -> OperationSetup:
    """Create or resume operation directory.

    Creates exactly one operation directory per operation_id.
    Reuses existing operation on repeated calls.
    """
    ops_root = _get_operations_root()
    operation_dir = ops_root / operation_id
    config_path = operation_dir / "operation-config.json"

    if operation_dir.exists() and config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
            operation_config = json.loads(config_text)
            return OperationSetup(
                operation_id=operation_id,
                operation_dir=operation_dir,
                operation_config=operation_config,
                stage_state=operation_config.get("stage_state", {}),
                already_existed=True,
            )
        except Exception:
            pass

    operation_dir.mkdir(parents=True, exist_ok=True)

    operation_config = {
        "operation_id": operation_id,
        "plan_id": plan_id,
        "repository_root": repository_root,
        "stage_state": {
            "intake": "completed",
            "operation_setup": "completed",
            "preserve_plan": "pending",
            "repository_evidence": "pending",
            "gap_discovery": "pending",
            "gap_resolution": "pending",
            "evaluation": "pending",
            "success_criteria": "pending",
            "validation": "pending",
            "fixtures": "pending",
            "companion_package": "pending",
        },
    }
    setup = OperationSetup(
        operation_id=operation_id,
        operation_dir=operation_dir,
        operation_config=operation_config,
        stage_state=operation_config["stage_state"],
        already_existed=False,
    )
    setup.persist_config()
    return setup


def update_stage_state(setup: OperationSetup, stage: str, state: str) -> None:
    """Update the stage state in operation config."""
    setup.stage_state[stage] = state
    setup.operation_config["stage_state"][stage] = state
    setup.persist_config()
