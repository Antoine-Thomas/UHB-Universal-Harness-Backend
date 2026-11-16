"""The tool catalogue: one definition per tool, and it never grants permission.

The catalogue answers "what is it, and why is it not available in this mode".
`POST /tools/execute` answers "may I", and it is the only thing that does — a
test here proves the listing cannot be used as a credential.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from uhb.hub.modes import get_mode
from uhb.tools import catalog


def test_every_catalogued_tool_is_the_callable_that_runs() -> None:
    """The catalogue cannot describe a tool the package does not expose."""
    import uhb.tools as tools_pkg

    for spec in catalog.specs():
        assert callable(getattr(tools_pkg, spec.name, None)), f"{spec.name} is described but absent"


def test_the_schema_is_a_valid_object_schema_derived_from_the_limits() -> None:
    spec = catalog.get("execute_code")
    assert spec is not None
    schema = spec.parameters

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])
    assert "code" in schema["properties"]

    from uhb.tools.execute import MAX_TIMEOUT

    timeout = schema["properties"]["timeout"]
    assert timeout["maximum"] == int(MAX_TIMEOUT), (
        "the described ceiling must be the enforced one, not a second number"
    )


def test_the_dev_mode_has_the_tool_and_says_nothing_else() -> None:
    payload = catalog.describe(name="execute_code", mode="dev")

    entry = payload["tool"]
    assert entry["available"] is True
    assert entry["reason"] is None
    assert entry["compatible_modes"] == ["dev"]
    assert entry["category"] == "code"
    assert entry["execution_type"] == "subprocess"
    assert entry["version"] and entry["enabled"] is True


def test_a_mode_without_the_tool_gets_a_structured_reason() -> None:
    payload = catalog.describe(name="execute_code", mode="chat")

    entry = payload["tool"]
    assert entry["available"] is False
    assert entry["reason"]["code"] == "not_in_mode"
    assert entry["reason"]["mode"] == "chat"
    assert entry["reason"]["available_in"] == ["dev"]
    assert entry["reason"]["hint"], "a code without a hint is a puzzle, not an answer"


def test_no_mode_means_information_only() -> None:
    """Without a mode the catalogue describes, it does not judge."""
    payload = catalog.describe(name="execute_code")

    entry = payload["tool"]
    assert payload["mode"] == ""
    assert entry["available"] is True
    assert entry["reason"] is None


def test_an_unknown_tool_lists_what_exists() -> None:
    with pytest.raises(catalog.UnknownTool) as caught:
        catalog.describe(name="rm_rf")

    assert caught.value.name == "rm_rf"
    assert "execute_code" in caught.value.known


def test_a_disabled_tool_says_so() -> None:
    from dataclasses import replace

    disabled = replace(catalog.EXECUTE_CODE, enabled=False)
    reason = catalog._reason(disabled, get_mode("dev"), ["dev"])

    assert reason == {"code": "disabled", "hint": "this tool is turned off in this build"}


@pytest.mark.parametrize("needle", ["UHB_RUNS_DIR", "C:\\", "sk-", "token", "uhb/data"])
def test_descriptions_carry_no_path_no_env_name_and_no_secret(needle: str) -> None:
    """The catalogue is served to a model, and a model repeats what it is told."""
    for spec in catalog.specs():
        blob = json.dumps(spec.to_dict(), ensure_ascii=False)
        assert needle not in blob, f"{spec.name} leaks {needle!r}"


# --------------------------------------------------------------------------- #
# the HTTP surface
# --------------------------------------------------------------------------- #
def _server(mode: str = "dev"):
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.mode
    server_module.Handler.mode = mode
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    return httpd, original


def _get(port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode())
    finally:
        conn.close()


def test_the_route_lists_the_catalogue_for_the_served_mode() -> None:
    httpd, original = _server("dev")
    try:
        status, data = _get(httpd.server_address[1], "/tools")
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 200
    assert data["mode"] == "dev"
    assert data["count"] >= 1
    assert data["request_id"], "every answer names the request it answers"
    entry = data["tools"][0]
    assert entry["available"] is True


def test_the_route_details_one_tool_and_refuses_a_mode_the_build_does_not_have() -> None:
    httpd, original = _server("chat")
    try:
        port = httpd.server_address[1]
        ok_status, ok = _get(port, "/tools/execute_code")
        bad_status, bad = _get(port, "/tools/execute_code?mode=readonly")
        missing_status, missing = _get(port, "/tools/nope")
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert ok_status == 200
    assert ok["tool"]["available"] is False
    assert ok["tool"]["reason"]["code"] == "not_in_mode"

    assert bad_status == 404
    assert bad["error"]["type"] == "unknown_mode"
    assert bad["error"]["hint"]

    assert missing_status == 404
    assert missing["error"]["type"] == "not_found"
    assert "execute_code" in missing["error"]["message"]


def test_listing_is_not_a_permission() -> None:
    """The exact defect this design forbids: reading the menu must not open a door."""
    httpd, original = _server("chat")
    try:
        port = httpd.server_address[1]
        # the catalogue publishes execute_code, with its reason
        _, listing = _get(port, "/tools?mode=dev")
        assert listing["tools"][0]["available"] is True
        # and the server still refuses to run it, because its own mode is chat
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request("POST", "/tools/execute",
                         body=json.dumps({"code": "print(1)", "mode": "dev"}).encode(),
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            status = response.status
            body = json.loads(response.read().decode())
        finally:
            conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 403
    assert body["error"]["type"] == "forbidden"


def _restore(original: str) -> None:
    from uhb.entrypoints import server as server_module

    server_module.Handler.mode = original
