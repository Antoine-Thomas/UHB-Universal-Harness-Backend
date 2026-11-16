"""``execute_code``: the gate, the containment, and what comes back.

No network, no model, no matplotlib: the snippets below use the standard library
only, and the image they write is a 1×1 PNG stored as bytes. That keeps the test
suite as fast as the rest of it while still pinning the real contract — a process
is spawned, the streams are captured, the files it produced are discovered.

The gate is tested as hard as the feature: a read-only mode must refuse *before*
a byte is written, and the child must not see the operator's API keys.
"""

from __future__ import annotations

import base64
import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from uhb.hub.modes import ModeRequired, ModeViolation, UnknownMode, get_mode
from uhb.tools import CodeExecutionError, execute_code
from uhb.tools import execute as tool

#: 1×1 transparent PNG (70 bytes). Written by the snippet, not by us.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

WRITE_PNG = "open('chart.png', 'wb').write(base64.b64decode(%r))" % (
    base64.b64encode(PNG_1X1).decode()
)


@pytest.fixture(autouse=True)
def runs_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every run goes under the test's tmp_path, never under ~/.uhb."""
    monkeypatch.setenv(tool.RUNS_ENV, str(tmp_path / "runs"))


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_the_dev_mode_declares_the_tool() -> None:
    dev = get_mode("dev")

    assert dev.allows_tool("execute_code")
    assert "execute_code" in dev.to_dict()["tools"]


def test_a_read_only_mode_is_refused_before_anything_runs(tmp_path: Path) -> None:
    workdir = tmp_path / "never-created"

    with pytest.raises(ModeViolation) as caught:
        execute_code("print('should not run')", mode="chat", workdir=workdir)

    assert "execute_code" in str(caught.value)
    assert not workdir.exists(), "the gate must answer before the directory exists"


def test_no_mode_is_refused() -> None:
    """A tool call with no envelope is a hole, not a default."""
    with pytest.raises(ModeRequired):
        execute_code("print(1)")


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(UnknownMode):
        execute_code("print(1)", mode="no-such-mode")


def test_empty_code_is_refused() -> None:
    with pytest.raises(CodeExecutionError):
        execute_code("   \n  ", mode="dev")


def test_a_timeout_below_the_floor_is_refused() -> None:
    with pytest.raises(CodeExecutionError):
        execute_code("print(1)", mode="dev", timeout=0)


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #
def test_a_snippet_prints_and_exits_zero() -> None:
    result = execute_code("print(2 + 2)", mode="dev")

    assert result.ok
    assert result.exit_code == 0
    assert result.stdout.strip() == "4"
    assert result.images == ()
    assert result.mode == "dev"


def test_a_failing_snippet_reports_the_code_and_the_traceback() -> None:
    result = execute_code("1 / 0", mode="dev")

    assert not result.ok
    assert result.exit_code != 0
    assert "ZeroDivisionError" in result.stderr


def test_the_runner_keeps_the_script_for_reproduction() -> None:
    code = "print('reproducible')"
    result = execute_code(code, mode="dev")

    assert (result.run_dir / "code.py").read_text(encoding="utf-8") == code


def test_runs_land_in_the_configured_directory(tmp_path: Path) -> None:
    result = execute_code("print(1)", mode="dev")

    assert result.run_dir.parent == tmp_path / "runs"


def test_a_timeout_kills_the_process_tree() -> None:
    started = time.monotonic()
    result = execute_code("import time\ntime.sleep(30)", mode="dev", timeout=tool.MIN_TIMEOUT)
    elapsed = time.monotonic() - started

    assert result.timed_out
    assert not result.ok
    assert elapsed < 20, "the child must be killed, not awaited"
    assert any("killed" in note for note in result.notes)


def test_stdout_is_truncated_with_an_explicit_note() -> None:
    result = execute_code("print('x' * 5000)", mode="dev", max_output=100)

    assert len(result.stdout) < 400
    assert "truncated" in result.stdout
    assert any("stdout truncated" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# the environment the snippet runs in
# --------------------------------------------------------------------------- #
def test_the_child_cannot_see_the_operator_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snippet must not be able to print a provider key into a transcript."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-DO-NOT-LEAK")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-DO-NOT-LEAK")

    result = execute_code("import os\nprint('\\n'.join(sorted(os.environ)))", mode="dev")

    assert result.ok
    assert "DEEPSEEK_API_KEY" not in result.stdout
    assert "OPENROUTER_API_KEY" not in result.stdout
    assert "DO-NOT-LEAK" not in result.stdout


def test_the_child_gets_a_headless_matplotlib_and_a_private_home() -> None:
    result = execute_code(
        "import os\nprint(os.environ['MPLBACKEND'])\nprint(os.environ['HOME'])\n"
        "print(os.environ['UHB_RUN_DIR'])",
        mode="dev",
    )

    backend, home, run_dir = result.stdout.strip().splitlines()
    assert backend == "Agg", "savefig must work with no display"
    assert Path(home) == Path(run_dir) == result.run_dir


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def test_an_image_is_found_with_its_type_and_size() -> None:
    result = execute_code(
        "import base64\n" + WRITE_PNG, mode="dev", include_base64=True
    )

    assert result.ok
    assert len(result.images) == 1
    image = result.images[0]
    assert image.relative == "chart.png"
    assert image.mime == "image/png"
    assert image.size == len(PNG_1X1)
    assert Path(image.path).read_bytes() == PNG_1X1
    assert image.data_url is not None and image.data_url.startswith("data:image/png;base64,")


def test_base64_is_off_by_default() -> None:
    result = execute_code("import base64\n" + WRITE_PNG, mode="dev")

    assert result.images and result.images[0].data_url is None
    assert result.images[0].path.is_file(), "the path is always reported"


def test_a_large_image_is_described_but_not_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "MAX_INLINE_BYTES", 10)

    result = execute_code("import base64\n" + WRITE_PNG, mode="dev", include_base64=True)

    assert result.images and result.images[0].data_url is None
    assert any("inline limit" in note for note in result.notes), "the reason is stated"


def test_svg_counts_as_an_image() -> None:
    result = execute_code(
        "open('diagram.svg', 'w').write('<svg xmlns=\"http://www.w3.org/2000/svg\"/>')",
        mode="dev",
    )

    assert [image.mime for image in result.images] == ["image/svg+xml"]


def test_an_image_in_a_subdirectory_is_found() -> None:
    result = execute_code(
        "import base64, os\nos.makedirs('figs', exist_ok=True)\n"
        "open('figs/plot.png', 'wb').write(base64.b64decode(%r))"
        % base64.b64encode(PNG_1X1).decode(),
        mode="dev",
    )

    assert [image.relative for image in result.images] == ["figs/plot.png"]


def test_a_second_run_does_not_report_the_previous_image(tmp_path: Path) -> None:
    workdir = tmp_path / "shared"
    first = execute_code("import base64\n" + WRITE_PNG, mode="dev", workdir=workdir)
    second = execute_code("print('nothing new')", mode="dev", workdir=workdir)

    assert [image.relative for image in first.images] == ["chart.png"]
    assert second.images == (), "an untouched file is not a new artefact"


def test_the_harness_files_are_never_reported_as_artefacts() -> None:
    result = execute_code("print('no image')", mode="dev")

    assert result.images == ()
    assert not any(name in str(result.images) for name in tool.HARNESS_FILES)


# --------------------------------------------------------------------------- #
# the HTTP route
# --------------------------------------------------------------------------- #
class _Server:
    def __init__(self, port: int, module) -> None:
        self.port = port
        self._module = module

    def serve_mode(self, mode: str) -> None:
        """The mode the next request is served under (Handler.mode is per request)."""
        self._module.Handler.mode = mode

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(
                "POST", path,
                body=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            conn.close()


@pytest.fixture()
def http_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from uhb.entrypoints import server as server_module

    monkeypatch.setenv(tool.RUNS_ENV, str(tmp_path / "runs"))
    original = server_module.Handler.mode
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    helper = _Server(httpd.server_address[1], server_module)
    try:
        yield helper
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.mode = original


def test_the_server_executes_as_its_own_mode(http_server: _Server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post(
        "/tools/execute",
        {"code": "import base64\n" + WRITE_PNG, "include_base64": True},
    )

    assert status == 200
    assert data["ok"] is True
    assert data["mode"] == "dev"
    assert data["images"][0]["data_url"].startswith("data:image/png;base64,")


def test_the_server_refuses_execution_in_a_read_only_mode(http_server: _Server) -> None:
    http_server.serve_mode("chat")

    status, data = http_server.post("/tools/execute", {"code": "print(1)"})

    assert status == 403
    assert "execute_code" in data["error"]["message"]


def test_the_server_refuses_to_lend_another_mode(http_server: _Server) -> None:
    """`--mode chat` must not become `dev` because the body asked for it."""
    http_server.serve_mode("chat")

    status, data = http_server.post("/tools/execute", {"code": "print(1)", "mode": "dev"})

    assert status == 403
    assert "chat" in data["error"]["message"]


def test_the_server_refuses_an_empty_code(http_server: _Server) -> None:
    http_server.serve_mode("dev")

    assert http_server.post("/tools/execute", {"code": "  "})[0] == 400
    assert http_server.post("/tools/execute", {})[0] == 400


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def test_the_cli_runs_a_snippet(capsys: pytest.CaptureFixture[str]) -> None:
    from uhb.entrypoints import cli

    assert cli.main(["tools", "exec", "--mode", "dev", "-c", "print('from the cli')", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stdout"].strip() == "from the cli"
    assert payload["ok"] is True


def test_the_cli_refuses_a_mode_without_the_tool(capsys: pytest.CaptureFixture[str]) -> None:
    from uhb.entrypoints import cli

    assert cli.main(["tools", "exec", "--mode", "chat", "-c", "print(1)"]) == cli.EXIT_USAGE
    assert "execute_code" in capsys.readouterr().err
