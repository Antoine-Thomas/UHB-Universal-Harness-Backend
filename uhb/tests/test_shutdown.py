"""``POST /shutdown``: the token, the gate, the order, and the proof it left nothing.

Every external effect is injected: the tests never talk to Docker and never signal
a real process. The one thing they do for real is stop the test server they
started — which is the behaviour under test.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from uhb.entrypoints import shutdown as sd


@pytest.fixture(autouse=True)
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A per-test token file: never the operator's ~/.uhb."""
    monkeypatch.delenv(sd.TOKEN_ENV, raising=False)
    path = tmp_path / "shutdown-token"
    monkeypatch.setenv(sd.TOKEN_FILE_ENV, str(path))
    return path


class FakeRunner:
    """Answers ``(argv, cwd)`` from a prefix table and records every call."""

    def __init__(self, replies: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.replies = replies or {}

    def __call__(self, argv: list[str], cwd):
        self.calls.append((list(argv), str(cwd) if cwd else None))
        joined = " ".join(argv)
        for prefix, reply in self.replies.items():
            if joined.startswith(prefix):
                return reply
        return 0, "", ""

    @property
    def commands(self) -> list[str]:
        return [" ".join(argv) for argv, _cwd in self.calls]


# --------------------------------------------------------------------------- #
# the token
# --------------------------------------------------------------------------- #
def test_the_environment_token_wins(token_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sd.TOKEN_ENV, "from-the-shell")

    assert sd.resolve_token() == "from-the-shell"
    assert sd.token_matches("from-the-shell")
    assert not token_file.exists(), "an explicit token must not be written to disk"


def test_a_generated_token_is_persisted_and_reused(token_file: Path) -> None:
    first = sd.resolve_token()

    assert len(first) >= 24
    assert token_file.read_text(encoding="utf-8").strip() == first
    assert sd.resolve_token() == first, "a restart must not rotate the operator's token"


def test_no_token_or_a_wrong_one_matches_nothing(token_file: Path) -> None:
    sd.resolve_token()

    assert sd.token_matches("") is False
    assert sd.token_matches(None) is False
    assert sd.token_matches("not-the-token") is False


# --------------------------------------------------------------------------- #
# who is ours
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["python.exe", "python3.11", "SiYuan-Kernel.exe", "uvicorn"])
def test_our_processes_are_recognised(name: str) -> None:
    assert sd.is_ours(sd.Listener(port=8200, pid=1, name=name))


@pytest.mark.parametrize("name", ["chrome.exe", "Code.exe", "postgres", "docker-proxy.exe"])
def test_other_peoples_processes_are_not(name: str) -> None:
    assert not sd.is_ours(sd.Listener(port=8200, pid=1, name=name))


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def test_the_plan_lists_containers_ours_and_refusals() -> None:
    runner = FakeRunner({"docker compose ps": (0, "uhb-uhb-1\nuhb-rag-1", "")})
    by_port = {
        8101: [sd.Listener(8101, 111, "python.exe")],
        8200: [sd.Listener(8200, 222, "chrome.exe")],
        6806: [],
    }

    discovered = sd.plan(targets=("docker", "local"), runner=runner, lister=lambda port: by_port[port])

    assert discovered.containers == ["uhb-uhb-1", "uhb-rag-1"]
    assert [listener.pid for listener in discovered.listeners] == [111]
    assert [listener.pid for listener in discovered.refused] == [222]
    assert discovered.to_dict()["docker"]["action"] == "docker compose down"


def test_a_plan_is_pure(tmp_path: Path) -> None:
    """Describing a shutdown must not stop anything."""
    runner = FakeRunner({"docker compose ps": (0, "uhb-uhb-1", "")})

    sd.plan(targets=("docker", "local"), runner=runner, lister=lambda port: [])

    assert runner.commands == ["docker compose ps --format {{.Name}}"]
    assert not any("down" in command for command in runner.commands)


# --------------------------------------------------------------------------- #
# stopping
# --------------------------------------------------------------------------- #
def test_docker_is_stopped_with_compose_down_and_verified() -> None:
    runner = FakeRunner({
        "docker compose ps --format": (0, "uhb-uhb-1\nuhb-siyuan-1", ""),
        "docker compose down": (0, "", ""),
    })
    # The second `ps` (the verification) must answer "nothing left".
    calls = {"ps": 0}

    def going(argv, cwd):
        joined = " ".join(argv)
        if joined.startswith("docker compose ps"):
            calls["ps"] += 1
            return (0, "uhb-uhb-1\nuhb-siyuan-1", "") if calls["ps"] == 1 else (0, "", "")
        return runner(argv, cwd)

    step = sd.stop_docker(runner=going)

    assert step.ok
    assert "stopped 2 container(s)" in step.detail
    assert any("down --timeout 10" in command for command in runner.commands)


def test_no_container_means_no_down_call() -> None:
    runner = FakeRunner({"docker compose ps": (0, "", "")})

    step = sd.stop_docker(runner=runner)

    assert step.ok
    assert "no container" in step.detail
    assert not any("down" in command for command in runner.commands)


def test_a_foreign_listener_is_reported_and_left_alone() -> None:
    runner = FakeRunner()

    steps = sd.stop_local(
        ports=(8200,), runner=runner, lister=lambda port: [sd.Listener(port, 222, "chrome.exe")],
        sleeper=lambda _s: None,
    )

    assert steps[0].ok is False
    assert "left alone" in steps[0].detail
    assert not any("taskkill" in command for command in runner.commands)


def test_our_listener_is_asked_to_leave_then_verified_gone() -> None:
    state = {"alive": True}
    calls: list[str] = []

    def runner(argv, cwd):
        calls.append(" ".join(argv))
        if argv[0] == "taskkill":
            state["alive"] = False      # the polite request worked
            return 0, "", ""
        if argv[0] == "tasklist":
            return (0, '"333","python.exe","1234","Console","1","10 K"', "") if state["alive"] \
                else (0, "INFO: No tasks are running which match the specified criteria.", "")
        return 0, "", ""

    steps = sd.stop_local(
        ports=(8200,), runner=runner, lister=lambda port: [sd.Listener(port, 333, "python.exe")],
        sleeper=lambda _s: None,
    )

    assert steps[0].ok is True
    assert "stopped after" in steps[0].detail
    forced = [c for c in calls if c.startswith("taskkill") and "/F" in c]
    assert forced == [], "a process that leaves on its own needs no force"


def test_a_stubborn_listener_is_forced_after_the_grace() -> None:
    forced: list[str] = []

    def runner(argv, cwd):
        joined = " ".join(argv)
        if joined.startswith("taskkill /F"):
            forced.append(joined)
            return 0, "", ""
        if joined.startswith("taskkill"):
            return 0, "", ""
        if joined.startswith("tasklist"):
            return 0, '"444","python.exe","1234","Console","1","10 K"', ""  # never dies
        return 0, "", ""

    steps = sd.stop_local(
        ports=(8101,), runner=runner, lister=lambda port: [sd.Listener(port, 444, "python.exe")],
        sleeper=lambda _s: None,
    )

    assert forced and "/F" in forced[0]
    assert steps[0].ok is False, "a process that survives the kill must be reported, not hidden"
    assert "STILL ALIVE" in steps[0].detail


def test_an_unknown_target_is_refused() -> None:
    with pytest.raises(sd.ShutdownError):
        sd.shutdown(targets=("everything",), runner=FakeRunner(), lister=lambda port: [])


def test_inside_a_container_only_self_is_possible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sd, "in_container", lambda: True)
    runner = FakeRunner()

    discovered = sd.plan(targets=("docker", "local"), runner=runner, lister=lambda port: [])
    report = sd.shutdown(targets=("docker", "local", "self"), runner=runner, lister=lambda port: [])

    assert discovered.containers == [] and discovered.listeners == []
    assert discovered.notes and "container" in discovered.notes[0]
    assert report["targets"] == ["self"]
    assert runner.commands == [], "a container cannot stop its host's containers or processes"


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
class _Server:
    def __init__(self, port: int) -> None:
        self.port = port

    def post(self, payload: dict, token: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers[sd.TOKEN_HEADER] = token
        try:
            conn.request("POST", "/shutdown", body=json.dumps(payload).encode(), headers=headers)
            response = conn.getresponse()
            body = response.read().decode()
            return response.status, (json.loads(body) if body else {})
        finally:
            conn.close()

    def reachable(self) -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
            conn.request("GET", "/status")
            conn.getresponse().read()
            conn.close()
            return True
        except OSError:
            return False


def _start(tmp_path: Path):
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.mode
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    return httpd, server_module, original


def test_the_route_refuses_without_a_token(token_file: Path) -> None:
    httpd, module, original = _start(token_file)
    try:
        status, data = _Server(httpd.server_address[1]).post({"confirm": True})
        assert status == 401
        assert sd.TOKEN_HEADER in data["error"]["header"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_the_route_refuses_without_an_explicit_confirmation(token_file: Path) -> None:
    token = sd.resolve_token()
    httpd, module, original = _start(token_file)
    try:
        server = _Server(httpd.server_address[1])
        assert server.post({"confirm": "yes"}, token=token)[0] == 403
        assert server.post({}, token=token)[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_a_dry_run_reports_the_plan_and_stops_nothing(token_file: Path) -> None:
    token = sd.resolve_token()
    httpd, module, original = _start(token_file)
    try:
        status, data = _Server(httpd.server_address[1]).post(
            {"confirm": True, "dry_run": True, "stop": ["self"]}, token=token
        )
        assert status == 200
        assert data["dry_run"] is True
        assert data["targets"] == ["self"]
        assert httpd.socket.fileno() != -1, "the server must still be listening"
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_an_unknown_target_is_a_400(token_file: Path) -> None:
    token = sd.resolve_token()
    httpd, module, original = _start(token_file)
    try:
        status, data = _Server(httpd.server_address[1]).post(
            {"confirm": True, "stop": ["everything"]}, token=token
        )
        assert status == 400
        assert "docker" in data["error"]["message"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_the_route_answers_202_then_the_server_is_gone(token_file: Path) -> None:
    """The interface must read "stopping", then see the socket close — in that order."""
    token = sd.resolve_token()
    httpd, module, original = _start(token_file)
    server = _Server(httpd.server_address[1])
    try:
        status, data = server.post({"confirm": True, "stop": ["self"]}, token=token)
        assert status == 202
        assert data["accepted"] is True
        assert data["self_stops_in_s"] < 2

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and server.reachable():
            time.sleep(0.2)
        assert not server.reachable(), "the server must actually be stopped"
    finally:
        try:
            httpd.server_close()
        finally:
            module.Handler.mode = original


def test_the_preflight_allows_the_token_header_for_a_loopback_dashboard(
    token_file: Path,
) -> None:
    """A dashboard on another loopback port must be able to preflight this POST.

    Measured: without `x-uhb-token` in ``Access-Control-Allow-Headers`` the browser
    blocked the request, and the button reported a network failure on a healthy
    server. The token, not the preflight, is what authorises the stop.
    """
    sd.resolve_token()
    httpd, module, original = _start(token_file)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request(
            "OPTIONS", "/shutdown",
            headers={
                "Origin": "http://127.0.0.1:8099",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-uhb-token",
            },
        )
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        conn.close()

        assert response.status == 204
        assert headers["access-control-allow-origin"] == "http://127.0.0.1:8099"
        assert "POST" in headers["access-control-allow-methods"]
        assert "x-uhb-token" in headers["access-control-allow-headers"].lower()
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_a_foreign_origin_gets_no_preflight_answer(token_file: Path) -> None:
    """The widened preflight stays loopback-only: another website gets nothing."""
    sd.resolve_token()
    httpd, module, original = _start(token_file)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request(
            "OPTIONS", "/shutdown",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        response.read()
        conn.close()

        assert response.status == 204
        assert "access-control-allow-origin" not in headers
    finally:
        httpd.shutdown()
        httpd.server_close()
        module.Handler.mode = original


def test_console_output_that_is_not_utf8_is_decoded_not_crashed() -> None:
    """Measured on a real stop: `tasklist` answers in cp850, and utf-8 decoding
    killed the reader thread — the branch crashed instead of stopping the process."""
    assert sd._decode("Processus arrêté".encode("cp850")) == "Processus arrêté"
    assert sd._decode(b"") == ""
    assert sd._decode(None) == ""
