from __future__ import annotations

import json

from aflow.config import save_local_ollama
from aflow.local_setup import DEFAULT_MODEL, setup_ollama


def test_local_config_contains_no_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = save_local_ollama(base_url="http://127.0.0.1:11434", model="qwen2.5-coder:7b")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5-coder:7b",
        "provider": "ollama",
    }


def test_existing_ollama_is_reused_without_install(tmp_path, monkeypatch):
    calls = []

    class Runner:
        def run(self, args, **kwargs):
            calls.append(args)

        def popen(self, args, **kwargs):
            calls.append(args)

    responses = iter([
        {"models": [{"name": DEFAULT_MODEL}]},
        {"response": "OK"},
    ])
    monkeypatch.setattr("aflow.local_setup._json_request", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("aflow.local_setup.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = setup_ollama(runner=Runner(), which=lambda name: "ollama" if name == "ollama" else "winget")
    assert result.model == DEFAULT_MODEL
    assert calls == []
