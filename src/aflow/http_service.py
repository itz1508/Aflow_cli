from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .core import ReviewError, SCHEMA_VERSION, review_plan


SERVICE_TOKEN_ENV = "AFLOW_SERVICE_TOKEN"
MAX_REQUEST_BYTES = 1_000_000
_REQUEST_KEYS_REQUIRED = frozenset({"original_plan", "plan_id"})
_REQUEST_KEYS_OPTIONAL = frozenset({"operation_id", "repository_root", "user_prompt", "plan_digest"})


def _error(status: int, code: str, message: str) -> tuple[int, dict[str, str]]:
    return status, {"error": code, "message": message}


def _is_valid_request(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if not _REQUEST_KEYS_REQUIRED.issubset(set(value.keys())):
        return False
    allowed = _REQUEST_KEYS_REQUIRED | _REQUEST_KEYS_OPTIONAL
    if not set(value.keys()).issubset(allowed):
        return False
    # required fields must be non-empty strings
    for k in _REQUEST_KEYS_REQUIRED:
        if not isinstance(value.get(k), str) or not str(value.get(k)).strip():
            return False
    # optional must be strings if present
    for k in _REQUEST_KEYS_OPTIONAL:
        if k in value and value[k] is not None and not isinstance(value[k], str):
            return False
    return True


def handle_request(
    *,
    method: str,
    path: str,
    authorization: str | None,
    body: bytes = b"",
    service_token: str,
) -> tuple[int, dict[str, Any]]:
    """Handle one local A-Flow HTTP request without writing a bundle to disk."""
    if method == "GET" and path == "/health":
        return 200, {"service": "aflow", "status": "ready", "schema_version": SCHEMA_VERSION}
    if method != "POST" or path != "/review":
        return _error(404, "not_found", "Use GET /health or POST /review.")
    expected_authorization = f"Bearer {service_token}"
    if not service_token or not authorization or not secrets.compare_digest(authorization, expected_authorization):
        return _error(401, "unauthorized", "A valid bearer token is required.")
    if len(body) > MAX_REQUEST_BYTES:
        return _error(413, "request_too_large", "The review request exceeds the size limit.")
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(400, "invalid_json", "The request body must be UTF-8 JSON.")
    if not _is_valid_request(request):
        return _error(400, "invalid_request", "Expected original_plan and plan_id strings with optional operation_id, repository_root, user_prompt, plan_digest.")
    try:
        operation_id = request.get("operation_id")
        repository_root = request.get("repository_root")
        user_prompt = request.get("user_prompt", "")
        plan_digest = request.get("plan_digest")
        if not repository_root:
            repository_root = str(Path.cwd())
        if not user_prompt:
            user_prompt = f"HTTP review of plan_id={request['plan_id']}"
        if not operation_id:
            operation_id = f"aflow-{request['plan_id']}"
        return 200, review_plan(
            original_plan=request["original_plan"],
            plan_id=request["plan_id"],
            plan_digest=plan_digest,
            operation_id=operation_id,
            repository_root=repository_root,
            user_prompt=user_prompt,
            force_canonical=True,
        )
    except ReviewError as exc:
        return _error(400, "invalid_request", str(exc))


def create_server(*, host: str, port: int, service_token: str) -> ThreadingHTTPServer:
    """Create the authenticated HTTP server for an embedding host or test."""
    token = service_token
    if not token:
        raise ReviewError("service_token must be non-empty")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(rendered)))
            self.end_headers()
            self.wfile.write(rendered)

        def do_GET(self) -> None:  # noqa: N802
            status, payload = handle_request(
                method="GET",
                path=self.path,
                authorization=self.headers.get("authorization"),
                service_token=token,
            )
            self._send(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            declared_length = self.headers.get("content-length")
            try:
                length = int(declared_length or "0")
            except ValueError:
                self._send(400, {"error": "invalid_request", "message": "content-length must be an integer."})
                return
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._send(413, {"error": "request_too_large", "message": "The review request exceeds the size limit."})
                return
            status, payload = handle_request(
                method="POST",
                path=self.path,
                authorization=self.headers.get("authorization"),
                body=self.rfile.read(length),
                service_token=token,
            )
            self._send(status, payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(*, host: str, port: int, service_token: str | None = None) -> None:
    token = service_token if service_token is not None else os.environ.get(SERVICE_TOKEN_ENV, "")
    if not token:
        raise ReviewError(f"{SERVICE_TOKEN_ENV} must be set before starting the HTTP service")
    create_server(host=host, port=port, service_token=token).serve_forever()
