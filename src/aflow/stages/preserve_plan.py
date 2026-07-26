"""Stage 3: Preserve original plan unchanged - binary exact."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def preserve_original_plan(*, operation_dir: Path, original_plan: str, original_bytes: bytes | None = None) -> dict[str, Any]:
    plan_path = operation_dir / "original-plan.md"
    # Use provided bytes if available to preserve exact CRLF, else utf-8 encode the string
    if original_bytes is not None:
        preserved_bytes = original_bytes
        preserved_content_str = preserved_bytes.decode("utf-8", errors="replace")
    else:
        preserved_content_str = original_plan
        preserved_bytes = preserved_content_str.encode("utf-8")

    if plan_path.exists():
        existing = plan_path.read_bytes()
        if existing == preserved_bytes:
            return {
                "preserved_at": str(plan_path),
                "original_plan_digest": hashlib.sha256(existing).hexdigest(),
            }

    temp = plan_path.with_suffix(".tmp")
    temp.write_bytes(preserved_bytes)
    os.replace(temp, plan_path)

    return {
        "preserved_at": str(plan_path),
        "original_plan_digest": hashlib.sha256(preserved_bytes).hexdigest(),
    }


def load_preserved_plan(*, operation_dir: Path) -> str:
    plan_path = operation_dir / "original-plan.md"
    if not plan_path.exists():
        raise FileNotFoundError(f"No preserved plan found at {plan_path}")
    return plan_path.read_bytes().decode("utf-8", errors="replace")

def load_preserved_bytes(*, operation_dir: Path) -> bytes:
    plan_path = operation_dir / "original-plan.md"
    if not plan_path.exists():
        raise FileNotFoundError(f"No preserved plan found at {plan_path}")
    return plan_path.read_bytes()
