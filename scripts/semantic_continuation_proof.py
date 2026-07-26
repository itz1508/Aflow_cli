"""Semantic truncation / continuation proof.

Part A (deterministic, injected model): demonstrates truncated-JSON detection,
bounded continuation requests, cross-round merge/de-duplication, stable
content-addressed observation ids, and section-coverage tracking.

Part B (one live local Ollama operation): runs the real semantic call once and
records sanitized provenance (fallback labeling, continuation rounds, observation
ids, covered sections). Never substitutes model output for repository evidence.

Sanitized evidence -> release-evidence/semantic-operation/continuation-proof/proof.json

Run: uv run python scripts/semantic_continuation_proof.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

EVIDENCE = REPO_ROOT / "release-evidence" / "semantic-operation" / "continuation-proof" / "proof.json"

LIVE_PLAN = (
    "## Authentication\n"
    "Objective: add token-based login.\n"
    "Success criteria: sessions expire.\n"
    "## Logging\n"
    "Objective: structured audit trail.\n"
    "Validation: pytest\n"
    "Fixture: sample request\n"
)


def _sequence(*responses):
    state = {"calls": 0}
    seq = list(responses)

    def generate(prompt):
        idx = min(state["calls"], len(seq) - 1)
        state["calls"] += 1
        return seq[idx]

    generate.state = state
    return generate


def _sanitize(value):
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if isinstance(value, str):
        out = value
        if home:
            out = out.replace(home, "<HOME>").replace(home.replace("\\", "/"), "<HOME>")
        if user:
            out = out.replace(user, "<USER>")
        return out
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def main() -> int:
    from aflow_cli.semantic import _stable_observation_id, local_semantic_notes

    ollama = {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5-coder:7b"}
    import aflow_cli.semantic as semantic_mod

    evidence: dict = {"proof_type": "semantic-truncation-continuation"}

    # --- Part A: deterministic injected-model demonstration ---
    original_load = semantic_mod.load_config
    semantic_mod.load_config = lambda: dict(ollama)  # type: ignore[assignment]
    try:
        gen = _sequence(
            {"response": '{"observations": ["authentication needs detail", "second o', "done_reason": "length"},
            {"response": 'bs about logging", "authentication needs detail", "third point"]}', "done_reason": "stop"},
        )
        notes, prov = local_semantic_notes(LIVE_PLAN, operation_id="semantic-proof-mock", generate=gen)
        evidence["deterministic_continuation"] = {
            "observed_calls": gen.state["calls"],
            "continuation_rounds": prov.continuation_rounds,
            "truncation_detected": prov.truncation_detected,
            "merged_observations": notes,
            "deduplicated": notes.count("authentication needs detail") == 1,
            "stable_ids": prov.observation_ids,
            "ids_are_content_addressed": prov.observation_ids == [_stable_observation_id(n) for n in notes],
            "covered_sections": prov.covered_sections,
        }

        # Bounded continuation: never-closing response stops after max.
        os.environ["AFLOW_SEMANTIC_MAX_CONTINUATIONS"] = "2"
        gen2 = _sequence({"response": '{"observations": ["never closes', "done_reason": "length"})
        _, prov2 = local_semantic_notes(LIVE_PLAN, generate=gen2)
        evidence["bounded_continuation"] = {
            "max_continuations": 2,
            "observed_calls": gen2.state["calls"],
            "stopped_at_bound": gen2.state["calls"] == 3,
            "continuation_rounds": prov2.continuation_rounds,
        }
        os.environ.pop("AFLOW_SEMANTIC_MAX_CONTINUATIONS", None)
    finally:
        semantic_mod.load_config = original_load  # type: ignore[assignment]

    # --- Part B: one live local Ollama operation (uses real config) ---
    live_notes, live_prov = local_semantic_notes(LIVE_PLAN, operation_id="semantic-proof-live")
    evidence["live_operation"] = _sanitize({
        "provider": live_prov.provider,
        "model": live_prov.model,
        "response_status": live_prov.response_status,
        "fallback_used": live_prov.fallback_used,
        "continuation_rounds": live_prov.continuation_rounds,
        "truncation_detected": live_prov.truncation_detected,
        "observation_count": len(live_notes),
        "observation_ids": live_prov.observation_ids,
        "covered_sections": live_prov.covered_sections,
        "raw_response_chars": live_prov.raw_response_chars,
        "note": "Observations are advisory only; they remain unresolved until repository evidence confirms them.",
    })

    evidence["deterministic_checks_passed"] = all([
        evidence["deterministic_continuation"]["observed_calls"] == 2,
        evidence["deterministic_continuation"]["continuation_rounds"] == 1,
        evidence["deterministic_continuation"]["truncation_detected"] is True,
        evidence["deterministic_continuation"]["deduplicated"] is True,
        evidence["deterministic_continuation"]["ids_are_content_addressed"] is True,
        "Authentication" in evidence["deterministic_continuation"]["covered_sections"],
        evidence["bounded_continuation"]["stopped_at_bound"] is True,
    ])

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_sanitize(evidence), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, EVIDENCE)
    print(json.dumps(_sanitize(evidence), indent=2))
    return 0 if evidence["deterministic_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
