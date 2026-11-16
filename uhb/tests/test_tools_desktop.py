"""Desktop tools: the OS accessibility tree, with refusals instead of guesses.

Three things are pinned here, and the third is the one that matters:

* a machine without a backend says `platform_unavailable` — it does not raise;
* `open_url` accepts http(s) and refuses `file:` and `javascript:`;
* an action on an unknown ref is `stale_ref`, never a click on whatever is at
  those coordinates. That is agent-desktop's lesson, and it is the difference
  between a harness and a macro recorder.

The real-tree tests skip when the optional backend is absent, so the suite stays
runnable on a machine with no UI Automation.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from uhb.tools import desktop

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "win32",
    reason="the only wired backend is Windows UI Automation",
)


def test_a_machine_without_the_extra_says_so(monkeypatch) -> None:
    monkeypatch.setattr(desktop, "_ui", lambda: None)

    status = desktop.backend_status()

    assert status["available"] is False
    assert status["reason"]["code"] == "backend_missing"
    assert "uhb[desktop]" in status["reason"]["hint"]


@pytest.mark.parametrize("platform,code", [("darwin", "platform_unavailable"),
                                           ("linux", "platform_unavailable")])
def test_the_other_platforms_are_not_pretended(monkeypatch, platform: str, code: str) -> None:
    monkeypatch.setattr(desktop.sys, "platform", platform)

    status = desktop.backend_status()

    assert status["available"] is False
    assert status["reason"]["code"] == code
    assert status["platform"] in ("macos", "linux")


def test_an_unsafe_url_is_refused_before_any_browser_call(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url, new=2: opened.append(url) or True)

    for unsafe in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "ftp://x/y", "", "wiki"):
        result = desktop.open_url(unsafe)
        assert result["ok"] is False, unsafe
        assert result["reason"]["code"] in ("unsafe_url", "invalid_url")
    assert opened == [], "nothing reached the browser"


def test_a_https_url_is_opened(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url, new=2: opened.append(url) or True)

    result = desktop.open_url("https://fr.wikipedia.org/wiki/Accueil")

    assert result["ok"] is True
    assert result["post_state"] == {"opened": True}
    assert opened == ["https://fr.wikipedia.org/wiki/Accueil"]


def test_an_unknown_ref_is_never_clicked() -> None:
    """Without a backend the answer is `backend_missing`, never a click.

    The stale-ref logic is checked below, on `_resolve`, so the guarantee is tested
    on a machine with no accessibility backend too — CI taught that the hard way.
    """
    result = desktop.click("deadbeef:0")

    assert result["ok"] is False
    assert result["reason"]["code"] in {"stale_ref", "backend_missing"}


def test_resolve_refuses_a_snapshot_it_no_longer_holds() -> None:
    node, error = desktop._resolve("deadbeef:0")

    assert node is None
    assert error is not None
    assert error["reason"]["code"] == "stale_ref"
    assert error["reason"]["ref"] == "deadbeef:0"


def test_resolve_refuses_a_ref_from_another_snapshot() -> None:
    read = desktop.snapshot(max_nodes=5)
    if not read.get("ok"):
        pytest.skip(f"no accessibility backend: {read.get('reason')}")

    node, error = desktop._resolve("00000000:0")

    assert node is None
    assert error is not None and error["reason"]["code"] == "stale_ref"


def test_find_without_text_asks_for_text() -> None:
    assert desktop.find("")["reason"]["code"] == "invalid_request"


# --------------------------------------------------------------------------- #
# the real tree: what this machine can actually read
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not desktop.backend_status()["available"], reason="no accessibility backend")
def test_windows_returns_the_desktop_it_can_see() -> None:
    result = desktop.windows()

    assert result["ok"] is True
    assert result["count"] == len(result["windows"])
    assert all({"name", "role", "bounds"} <= set(window) for window in result["windows"])


@pytest.mark.skipif(not desktop.backend_status()["available"], reason="no accessibility backend")
def test_a_snapshot_hands_out_refs_that_belong_to_it() -> None:
    result = desktop.snapshot(max_nodes=30)

    assert result["ok"] is True
    snapshot_id = result["snapshot_id"]
    assert result["elements"], "a desktop with no element at all is not a desktop"
    for element in result["elements"]:
        assert element["ref"].startswith(f"{snapshot_id}:"), "a ref names its snapshot"


# --------------------------------------------------------------------------- #
# the HTTP surface
# --------------------------------------------------------------------------- #
def _server(mode: str):
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.mode
    server_module.Handler.mode = mode
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    return httpd, original


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", path, body=json.dumps(payload).encode(),
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode())
    finally:
        conn.close()


def _restore(original: str) -> None:
    from uhb.entrypoints import server as server_module

    server_module.Handler.mode = original


@pytest.mark.skipif(not desktop.backend_status()["available"], reason="no accessibility backend")
def test_the_route_calls_a_desktop_tool_when_the_mode_allows_it() -> None:
    httpd, original = _server("dev")
    try:
        status, data = _post(httpd.server_address[1], "/tools/desktop_windows", {})
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 200
    assert data["ok"] is True
    assert data["request_id"]


def test_the_route_refuses_a_desktop_tool_the_mode_does_not_declare(capsys) -> None:
    httpd, original = _server("chat")
    try:
        status, data = _post(httpd.server_address[1], "/tools/desktop_click", {"ref": "x:0"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 403
    assert data["error"]["type"] == "forbidden"
    assert "[uhb] tools/execute refused (403)" in capsys.readouterr().out


def test_the_route_refuses_an_unsafe_url_with_a_status_the_client_can_read() -> None:
    httpd, original = _server("dev")
    try:
        status, data = _post(httpd.server_address[1], "/tools/desktop_open_url",
                             {"url": "file:///etc/passwd"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 400
    assert data["reason"]["code"] == "unsafe_url"
    assert data["request_id"]


def test_the_route_does_not_know_a_tool_it_was_not_told_about() -> None:
    httpd, original = _server("dev")
    try:
        status, data = _post(httpd.server_address[1], "/tools/rm_rf", {})
    finally:
        httpd.shutdown()
        httpd.server_close()
        _restore(original)

    assert status == 404
    assert data["error"]["type"] == "not_found"
    assert "desktop_click" in data["error"]["message"]
