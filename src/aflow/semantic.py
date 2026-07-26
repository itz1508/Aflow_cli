from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

from .config import load_config
from .semantic_provenance import SemanticProvenance, persist_semantic_provenance

FALLBACK_UNAVAILABLE = "Local semantic review was unavailable; deterministic review completed without substituting remote evidence."
FALLBACK_TIMEOUT = "Local semantic review timed out; deterministic review completed without substituting remote evidence."

_OBSERVATION_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plan_sections(plan: str) -> list[str]:
    """Return the ordered section titles (## headings) of a plan."""
    sections: list[str] = []
    for line in plan.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and len(stripped) > 3:
            sections.append(stripped[3:].strip())
    return sections


def _normalize_key(text: str) -> str:
    return " ".join(text.split()).lower()


def _stable_observation_id(text: str) -> str:
    digest = hashlib.sha1(_normalize_key(text).encode("utf-8")).hexdigest()[:12]
    return f"semantic-observation-{digest}"


def _extract_observations(raw_text: str) -> tuple[list[str], bool]:
    """Extract observation strings from a (possibly truncated) model response.

    Returns (observations, complete). When strict JSON parsing succeeds the
    response is complete; otherwise complete strings are salvaged from the
    partial ``observations`` array and complete is False so a continuation can
    be requested.
    """
    text = (raw_text or "").strip()
    if not text:
        return [], False
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            values = parsed.get("observations", [])
        elif isinstance(parsed, list):
            values = parsed
        else:
            values = []
        observations = [str(v).strip() for v in values if isinstance(v, str) and str(v).strip()]
        return observations, True
    except json.JSONDecodeError:
        pass

    # Salvage: only look inside the observations array, and only accept strings
    # that are fully closed (a dangling final string is dropped as incomplete).
    marker = text.find("observations")
    scope = text[marker:] if marker != -1 else text
    array_start = scope.find("[")
    if array_start != -1:
        scope = scope[array_start + 1:]
    salvaged: list[str] = []
    for match in _OBSERVATION_STRING.finditer(scope):
        try:
            salvaged.append(json.loads('"' + match.group(1) + '"').strip())
        except json.JSONDecodeError:
            continue
    salvaged = [s for s in salvaged if s]
    return salvaged, False


def _merge_dedupe(*groups: list[str]) -> list[str]:
    """Order-preserving merge that drops duplicate observations by normalized text."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group:
            key = _normalize_key(item)
            if key and key not in seen:
                seen.add(key)
                merged.append(item.strip())
    return merged


def _covered_sections(observations: list[str], sections: list[str]) -> list[str]:
    blob = " ".join(observations).lower()
    covered = []
    for section in sections:
        tokens = [t for t in re.split(r"\W+", section.lower()) if len(t) > 3]
        if tokens and any(token in blob for token in tokens):
            covered.append(section)
    return covered


def _continuation_prompt(partial: str) -> str:
    return (
        "Your previous JSON response was cut off before the observations array was closed. "
        "Continue exactly where you stopped and output only the remaining JSON so the full "
        "response parses as {\"observations\": [...]}. Do not repeat earlier observations.\n\n"
        "PARTIAL RESPONSE SO FAR:\n" + partial
    )


def _base_prompt(original_plan: str) -> str:
    return (
        "Review this plan only. Do not propose a new design, edit the plan, or claim evidence. "
        "Return JSON with an observations array of at most five short strings.\n\nPLAN:\n" + original_plan
    )


def _make_generate(base_url: str, model: str, timeout: int) -> Callable[[str], dict[str, Any]]:
    def generate(prompt: str) -> dict[str, Any]:
        request = Request(
            base_url.rstrip("/") + "/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return generate


def local_semantic_notes(
    original_plan: str,
    operation_id: str | None = None,
    operation_dir_path=None,
    generate: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[list[str], SemanticProvenance | None]:
    """Return clearly-labelled, non-evidentiary local-model observations with provenance.

    Handles truncated / incomplete model output by requesting bounded
    continuations, then merges and de-duplicates observations with stable ids.
    ``generate`` may be injected for tests; production uses a local Ollama call.
    """
    config = load_config()
    provider = config.get("provider", "unknown")
    base_url = config.get("base_url", "")
    model = config.get("model", "")

    started_at = _now()

    def _finish(provenance: SemanticProvenance, notes: list[str]):
        provenance.duration_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()
        if operation_dir_path:
            persist_semantic_provenance(operation_dir_path, provenance)
        return notes, provenance

    if provider != "ollama":
        return _finish(
            SemanticProvenance(operation_id=operation_id or "unknown", provider=provider, endpoint=base_url, model=model, started_at=started_at, completed_at=_now(), duration_seconds=0.0, response_status="skipped_wrong_provider", fallback_used=True, error_details=f"Provider is {provider}, not ollama"),
            [],
        )

    if not isinstance(base_url, str) or not isinstance(model, str):
        return _finish(
            SemanticProvenance(operation_id=operation_id or "unknown", provider=provider, endpoint=base_url, model=model, started_at=started_at, completed_at=_now(), duration_seconds=0.0, response_status="skipped_invalid_config", fallback_used=True, error_details="Invalid base_url or model configuration"),
            [],
        )

    timeout = int(os.environ.get("AFLOW_SEMANTIC_TIMEOUT", "120"))
    max_continuations = int(os.environ.get("AFLOW_SEMANTIC_MAX_CONTINUATIONS", "3"))
    if generate is None:
        generate = _make_generate(base_url, model, timeout)

    sections = _plan_sections(original_plan)

    try:
        raw = ""
        observations: list[str] = []
        truncation_detected = False
        calls = 0
        prompt = _base_prompt(original_plan)
        while True:
            payload = generate(prompt)
            calls += 1
            raw += str(payload.get("response", ""))
            done_reason = payload.get("done_reason")
            extracted, complete = _extract_observations(raw)
            observations = _merge_dedupe(extracted)
            incomplete = (not complete) or (done_reason == "length")
            if incomplete:
                truncation_detected = True
            if not incomplete or len(observations) >= 5 or (calls - 1) >= max_continuations:
                break
            prompt = _continuation_prompt(raw)

        observations = observations[:5]
        provenance = SemanticProvenance(
            operation_id=operation_id or "unknown",
            provider=provider,
            endpoint=base_url,
            model=model,
            started_at=started_at,
            completed_at=_now(),
            duration_seconds=0.0,
            response_status="success",
            model_generated_observations=observations,
            fallback_used=False,
            continuation_rounds=calls - 1,
            truncation_detected=truncation_detected,
            observation_ids=[_stable_observation_id(o) for o in observations],
            covered_sections=_covered_sections(observations, sections),
            raw_response_chars=len(raw),
        )
        return _finish(provenance, observations)
    except TimeoutError:
        return _finish(
            SemanticProvenance(operation_id=operation_id or "unknown", provider=provider, endpoint=base_url, model=model, started_at=started_at, completed_at=_now(), duration_seconds=0.0, response_status="timed_out", fallback_used=True, error_details="timed out"),
            [FALLBACK_TIMEOUT],
        )
    except Exception as exc:  # noqa: BLE001 - deterministic fallback on any transport/parse failure
        return _finish(
            SemanticProvenance(operation_id=operation_id or "unknown", provider=provider, endpoint=base_url, model=model, started_at=started_at, completed_at=_now(), duration_seconds=0.0, response_status="unavailable", fallback_used=True, error_details=str(exc)),
            [FALLBACK_UNAVAILABLE],
        )
