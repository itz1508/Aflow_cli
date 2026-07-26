from __future__ import annotations

import json

import pytest

from aflow_cli.semantic import (
    _extract_observations,
    _merge_dedupe,
    _stable_observation_id,
    local_semantic_notes,
)
from aflow_cli.semantic_provenance import load_semantic_provenance
from aflow_cli.stages.gap_discovery import discover_gaps

OLLAMA_CONFIG = {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "test-model"}


@pytest.fixture(autouse=True)
def _force_ollama(monkeypatch):
    monkeypatch.setattr("aflow_cli.semantic.load_config", lambda: dict(OLLAMA_CONFIG))


def _sequence(*responses):
    """Return a generate() callable yielding the given payloads in order, counting calls."""
    state = {"calls": 0}
    seq = list(responses)

    def generate(prompt):
        idx = min(state["calls"], len(seq) - 1)
        state["calls"] += 1
        return seq[idx]

    generate.state = state  # type: ignore[attr-defined]
    return generate


def test_complete_response_uses_single_call():
    gen = _sequence({"response": json.dumps({"observations": ["Alpha", "Beta"]}), "done_reason": "stop"})
    notes, prov = local_semantic_notes("## Section\nbody", generate=gen)
    assert notes == ["Alpha", "Beta"]
    assert prov.response_status == "success"
    assert prov.continuation_rounds == 0
    assert prov.truncation_detected is False
    assert gen.state["calls"] == 1


def test_truncated_json_triggers_continuation_and_merges():
    gen = _sequence(
        {"response": '{"observations": ["first obs", "second o', "done_reason": "length"},
        {"response": 'bs", "third obs"]}', "done_reason": "stop"},
    )
    notes, prov = local_semantic_notes("## Section\nbody", generate=gen)
    assert notes == ["first obs", "second obs", "third obs"]
    assert prov.truncation_detected is True
    assert prov.continuation_rounds == 1
    assert gen.state["calls"] == 2


def test_duplicate_observations_are_deduplicated_across_rounds():
    gen = _sequence(
        {"response": '{"observations": ["dup obs", "unique one"', "done_reason": "length"},
        {"response": ', "DUP OBS", "unique two"]}', "done_reason": "stop"},
    )
    notes, _ = local_semantic_notes("## Section\nbody", generate=gen)
    assert notes == ["dup obs", "unique one", "unique two"]


def test_continuation_is_bounded_by_max(monkeypatch):
    monkeypatch.setenv("AFLOW_SEMANTIC_MAX_CONTINUATIONS", "2")
    # Always incomplete: dangling unclosed string + done_reason length.
    gen = _sequence({"response": '{"observations": ["never closes', "done_reason": "length"})
    notes, prov = local_semantic_notes("## Section\nbody", generate=gen)
    # 1 initial call + at most 2 continuations.
    assert gen.state["calls"] == 3
    assert prov.truncation_detected is True
    assert prov.continuation_rounds == 2


def test_stable_ids_are_content_addressed():
    assert _stable_observation_id("Same Text") == _stable_observation_id("same   text")
    assert _stable_observation_id("A") != _stable_observation_id("B")
    gen = _sequence({"response": json.dumps({"observations": ["Repeatable"]}), "done_reason": "stop"})
    _, prov = local_semantic_notes("## S\nx", generate=gen)
    assert prov.observation_ids == [_stable_observation_id("Repeatable")]


def test_section_coverage_is_recorded():
    plan = "## Authentication\nlogin flow\n## Logging\naudit trail\n"
    gen = _sequence({"response": json.dumps({"observations": ["The authentication step needs detail"]}), "done_reason": "stop"})
    _, prov = local_semantic_notes(plan, generate=gen)
    assert "Authentication" in prov.covered_sections
    assert "Logging" not in prov.covered_sections


def test_provenance_persisted_with_new_fields(tmp_path):
    gen = _sequence({"response": json.dumps({"observations": ["Persisted"]}), "done_reason": "stop"})
    local_semantic_notes("## S\nx", operation_id="op-1", operation_dir_path=tmp_path, generate=gen)
    loaded = load_semantic_provenance(tmp_path)
    assert loaded is not None
    assert loaded.observation_ids == [_stable_observation_id("Persisted")]
    assert loaded.raw_response_chars > 0


def test_exception_yields_labeled_fallback(tmp_path):
    def boom(prompt):
        raise RuntimeError("connection refused")

    notes, prov = local_semantic_notes("## S\nx", operation_dir_path=tmp_path, generate=boom)
    assert notes == ["Local semantic review was unavailable; deterministic review completed without substituting remote evidence."]
    assert prov.fallback_used is True
    assert prov.response_status == "unavailable"


def test_wrong_provider_is_skipped(monkeypatch):
    monkeypatch.setattr("aflow_cli.semantic.load_config", lambda: {"provider": "openai", "base_url": "x", "model": "y"})
    notes, prov = local_semantic_notes("## S\nx")
    assert notes == []
    assert prov.response_status == "skipped_wrong_provider"
    assert prov.fallback_used is True


def test_observations_gate_as_unresolved_repository_evidence():
    gaps = discover_gaps(original_plan="## S\nx", user_prompt="p", semantic_observations=["model says something"])
    semantic = [g for g in gaps if g.origin == "semantic"]
    assert semantic and semantic[0].resolution_status == "unresolved"
    assert semantic[0].required_evidence == "repository-evidence-required"
    # Fallback text must never become an advisory gap.
    none_gaps = discover_gaps(
        original_plan="## S\nx",
        user_prompt="p",
        semantic_observations=["Local semantic review was unavailable; deterministic review completed without substituting remote evidence."],
    )
    assert [g for g in none_gaps if g.origin == "semantic"] == []


def test_extract_and_merge_helpers_are_pure():
    obs, complete = _extract_observations('{"observations": ["x", "y"]}')
    assert obs == ["x", "y"] and complete is True
    partial, complete = _extract_observations('{"observations": ["x", "unclo')
    assert partial == ["x"] and complete is False
    assert _merge_dedupe(["a", "A"], ["b", "a"]) == ["a", "b"]
