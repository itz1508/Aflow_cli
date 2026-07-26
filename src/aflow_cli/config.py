from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "AFlow" / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    selected = path or config_path()
    value: dict[str, Any] = {}
    if selected.exists():
        value = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("A-Flow configuration must be an object")
    value.setdefault("provider", os.environ.get("AFLOW_PROVIDER", "ollama"))
    value.setdefault("base_url", os.environ.get("AFLOW_BASE_URL", "http://127.0.0.1:11434"))
    value.setdefault("model", os.environ.get("AFLOW_MODEL", "qwen2.5-coder:7b"))
    return value


def save_local_ollama(*, base_url: str, model: str, path: Path | None = None) -> Path:
    selected = path or config_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    content = {"provider": "ollama", "base_url": base_url, "model": model}
    temporary = selected.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, selected)
    return selected
