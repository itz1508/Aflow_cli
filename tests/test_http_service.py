from __future__ import annotations

import json
import threading
from urllib.request import Request, urlopen

from aflow_cli.core import validate_bundle
from aflow_cli.http_service import create_server, handle_request


PLAN = "Objective: test HTTP review\nSuccess criteria: immutable\nValidation: bundle\nFixture: request\n"
TOKEN = "test-service-token"


def _request(*, body: object, authorization: str | None = f"Bearer {TOKEN}"):
    encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return handle_request(
        method="POST",
        path="/review",
        authorization=authorization,
        body=encoded,
        service_token=TOKEN,
    )


def test_health_is_available_without_service_token():
    status, body = handle_request(method="GET", path="/health", authorization=None, service_token=TOKEN)
    assert status == 200
    assert body["status"] == "ready"


def test_review_rejects_missing_or_wrong_bearer_token():
    payload = {"original_plan": PLAN, "plan_id": "http-test"}
    assert _request(body=payload, authorization=None)[0] == 401
    assert _request(body=payload, authorization="Bearer wrong")[0] == 401


def test_review_rejects_unknown_fields():
    payload = {"original_plan": PLAN, "plan_id": "http-test", "extra": True}
    assert _request(body=payload)[0] == 400


def test_review_returns_a_valid_immutable_bundle():
    payload = {"original_plan": PLAN, "plan_id": "http-test"}
    status, body = _request(body=payload)
    assert status == 200
    assert body["manifest"]["plan_id"] == "http-test"
    assert validate_bundle(body)["valid"] is True


def test_http_listener_serves_authenticated_review():
    server = create_server(host="127.0.0.1", port=0, service_token=TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base_url + "/health", timeout=2) as response:
            assert json.loads(response.read())["status"] == "ready"
        payload = json.dumps({"original_plan": PLAN, "plan_id": "listener-test"}).encode("utf-8")
        request = Request(base_url + "/review", data=payload, method="POST", headers={"content-type": "application/json", "authorization": f"Bearer {TOKEN}"})
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read())
        assert result["manifest"]["plan_id"] == "listener-test"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
