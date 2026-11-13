"""The planning surface: GET /modes and POST /route, with no model involved.

`/route` must never reach an adapter: it plans, it does not run. The fixture
installs an adapter that fails the test if a turn is attempted, so a regression
that silently spends a token shows up as a failed assertion rather than a bill.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest


class ExplodingAdapter:
    """Any turn would fail the test: /route must never reach an adapter."""

    async def execute_task(self, prompt, context):
        raise AssertionError("POST /route ran a turn")
        yield  # pragma: no cover - unreachable


@pytest.fixture()
def server():
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.adapter
    server_module.Handler.adapter = ExplodingAdapter()
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.adapter = original


def _call(port: int, method: str, path: str, payload=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode() if payload is not None else None
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = json.loads(response.read().decode() or "null")
    conn.close()
    return response.status, data


JSON = {"Content-Type": "application/json"}


# --- GET /modes ------------------------------------------------------------ #


def test_modes_lists_every_mode_with_its_permissions(server):
    status, data = _call(server, "GET", "/modes")

    assert status == 200
    names = [entry["name"] for entry in data["modes"]]
    assert "chat" in names and names == sorted(names)
    assert data["count"] == len(names)

    chat = next(entry for entry in data["modes"] if entry["name"] == "chat")
    for key in ("read", "write", "deny", "tools", "system_prompt", "model"):
        assert key in chat
    assert chat["write"] == []  # chat never writes
    assert chat["model"]["tier"]


def test_modes_is_the_probe_the_harness_uses(server):
    """`isinstance(payload["modes"], list)` is the readiness test a client runs."""
    status, data = _call(server, "GET", "/modes")
    assert status == 200
    assert isinstance(data.get("modes"), list)


def test_modes_never_leaks_a_file_path(server):
    _status, data = _call(server, "GET", "/modes")
    assert all("source" not in entry for entry in data["modes"])


def test_modes_answers_a_loopback_browser(server):
    status, data = _call(server, "GET", "/modes", headers={"Origin": "http://localhost:3000"})
    assert status == 200
    assert data["modes"]


# --- POST /route ----------------------------------------------------------- #


def test_route_returns_the_plan_without_running_it(server):
    status, data = _call(server, "POST", "/route",
                         {"prompt": "corrige le bug de sandbox", "mode": "chat"}, JSON)

    assert status == 200
    assert data["mode"] == "chat"
    assert data["chain"] and data["chain"][0]["ref"]
    assert data["reason"].startswith("chat/")
    assert data["signals"]


def test_route_defaults_to_the_envelope_mode(server):
    status, data = _call(server, "POST", "/route", {"prompt": "hello"}, JSON)
    assert status == 200
    assert data["mode"] == "chat"


def test_route_can_be_forced_to_a_complexity(server):
    status, data = _call(server, "POST", "/route",
                         {"prompt": "hello", "complexity": "complex"}, JSON)
    assert status == 200
    assert data["complexity"] == "complex"
    assert data["signals"] == {"forced": 1}


def test_route_rejects_an_unknown_complexity(server):
    status, data = _call(server, "POST", "/route",
                         {"prompt": "hello", "complexity": "huge"}, JSON)
    assert status == 400
    assert "expected one of" in data["error"]["message"]


def test_route_names_the_known_modes_on_a_typo(server):
    status, data = _call(server, "POST", "/route", {"prompt": "x", "mode": "nope"}, JSON)
    assert status == 400
    message = data["error"]["message"]
    assert "known modes" in message
    assert not message.startswith('"'), "KeyError must not wrap the message in quotes"


def test_route_needs_a_prompt(server):
    status, data = _call(server, "POST", "/route", {"mode": "chat"}, JSON)
    assert status == 400
    assert "prompt" in data["error"]["message"]


def test_route_is_post_only(server):
    status, _data = _call(server, "GET", "/route")
    assert status == 404


def test_route_refuses_a_foreign_origin(server):
    # No body on purpose: the refusal happens before a byte is read, and closing
    # a socket with unread data resets the connection on Windows.
    status, data = _call(server, "POST", "/route", None,
                         {**JSON, "Origin": "https://evil.example"})
    assert status == 403
    assert "cross-origin" in data["error"]["message"]


def test_route_requires_json(server):
    status, data = _call(server, "POST", "/route", None, {"Content-Type": "text/plain"})
    assert status == 415
    assert "Content-Type" in data["error"]["message"]
