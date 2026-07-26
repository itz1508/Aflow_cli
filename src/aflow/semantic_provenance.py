"""Semantic provenance tracking for operation-local model calls."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SemanticProvenance:
    """Operation-local semantic execution record without secrets."""
    operation_id: str
    provider: str
    endpoint: str
    model: str
    started_at: str
    completed_at: str
    duration_seconds: float
    response_status: str
    model_generated_observations: list[str] = field(default_factory=list)
    fallback_used: bool = False
    error_details: str | None = None
    continuation_rounds: int = 0
    truncation_detected: bool = False
    observation_ids: list[str] = field(default_factory=list)
    covered_sections: list[str] = field(default_factory=list)
    raw_response_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model": self.model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "response_status": self.response_status,
            "model_generated_observations": self.model_generated_observations,
            "fallback_used": self.fallback_used,
            "error_details": self.error_details,
            "continuation_rounds": self.continuation_rounds,
            "truncation_detected": self.truncation_detected,
            "observation_ids": self.observation_ids,
            "covered_sections": self.covered_sections,
            "raw_response_chars": self.raw_response_chars,
        }


def persist_semantic_provenance(operation_dir: Path, provenance: SemanticProvenance) -> Path:
    """Persist semantic provenance to operation directory."""
    provenance_path = operation_dir / "semantic-provenance.json"
    temp_path = provenance_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(provenance.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, provenance_path)
    return provenance_path


def load_semantic_provenance(operation_dir: Path) -> SemanticProvenance | None:
    """Load semantic provenance if it exists."""
    provenance_path = operation_dir / "semantic-provenance.json"
    if not provenance_path.exists():
        return None
    data = json.loads(provenance_path.read_text(encoding="utf-8"))
    return SemanticProvenance(
        operation_id=data["operation_id"],
        provider=data["provider"],
        endpoint=data["endpoint"],
        model=data["model"],
        started_at=data["started_at"],
        completed_at=data["completed_at"],
        duration_seconds=data["duration_seconds"],
        response_status=data["response_status"],
        model_generated_observations=data.get("model_generated_observations", []),
        fallback_used=data.get("fallback_used", False),
        error_details=data.get("error_details"),
        continuation_rounds=data.get("continuation_rounds", 0),
        truncation_detected=data.get("truncation_detected", False),
        observation_ids=data.get("observation_ids", []),
        covered_sections=data.get("covered_sections", []),
        raw_response_chars=data.get("raw_response_chars", 0),
    )
