from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.request import Request, urlopen

from .config import save_local_ollama


OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class SetupError(RuntimeError):
    pass


class Runner(Protocol):
    def run(self, args: list[str], **kwargs): ...
    def popen(self, args: list[str], **kwargs): ...


class SubprocessRunner:
    run = staticmethod(subprocess.run)
    popen = staticmethod(subprocess.Popen)


@dataclass(frozen=True)
class SetupResult:
    ollama_path: str
    model: str
    endpoint: str


def _json_request(path: str, *, payload: dict | None = None, timeout: int = 5) -> dict | None:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(OLLAMA_URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def setup_ollama(*, runner: Runner | None = None, which: Callable[[str], str | None] = shutil.which, model: str = DEFAULT_MODEL, sleep: Callable[[float], None] = time.sleep) -> SetupResult:
    if sys.platform != "win32":
        raise SetupError("Windows 11 x64 is the supported setup platform for this release")
    selected = runner or SubprocessRunner()
    executable = which("ollama")
    if executable is None:
        if which("winget") is None:
            raise SetupError("WinGet is required to install Ollama automatically")
        try:
            selected.run(["winget", "install", "--id", "Ollama.Ollama", "-e", "--accept-source-agreements", "--accept-package-agreements"], check=True)
        except Exception as exc:
            raise SetupError("Ollama installation failed") from exc
        executable = which("ollama")
        if executable is None:
            raise SetupError("Ollama installed but is not on PATH; open a new terminal and run aflow setup again")
    tags = _json_request("/api/tags")
    if tags is None:
        selected.popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            sleep(0.25)
            tags = _json_request("/api/tags")
            if tags is not None:
                break
    if tags is None:
        raise SetupError("Ollama service is unavailable")
    names = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
    if model not in names:
        try:
            selected.run([executable, "pull", model], check=True)
        except Exception as exc:
            raise SetupError("Ollama model download failed") from exc
    probe = _json_request("/api/generate", payload={"model": model, "prompt": "Reply exactly OK.", "stream": False}, timeout=60)
    if not probe or not str(probe.get("response", "")).strip():
        raise SetupError("Ollama model verification failed")
    save_local_ollama(base_url=OLLAMA_URL, model=model)
    return SetupResult(executable, model, OLLAMA_URL)
