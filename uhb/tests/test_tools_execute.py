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

WRITE_PNG = f"open('chart.png', 'wb').write(base64.b64decode({base64.b64encode(PNG_1X1).decode()!r}))"


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
    """A snippet must not be able to print a provider key into a transcript.

    Two properties, both checked: no value leaks, and no variable *name* carrying a
    secret word survives the allow-list at all. The last part matters — a variable
    that is merely blanked still tells a snippet which account to target.
    """
    leaked = {
        "DEEPSEEK_API_KEY": "sk-live-DO-NOT-LEAK-1",
        "OPENROUTER_API_KEY": "or-live-DO-NOT-LEAK-2",
        "ANTHROPIC_API_KEY": "ant-live-DO-NOT-LEAK-3",
        "GH_TOKEN": "ghp-live-DO-NOT-LEAK-4",
        "GITHUB_TOKEN": "github-live-DO-NOT-LEAK-5",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-LIVE-DO-NOT-LEAK-6",
        "AWS_ACCESS_KEY_ID": "AKIALIVE-DO-NOT-LEAK-7",
        "UHB_SHUTDOWN_TOKEN": "token-should-never-be-visible-8",
        "UFW_PASSWORD": "pw-should-never-be-visible-9",
        "SOME_CREDENTIAL": "cred-should-never-be-visible-10",
    }
    for name, value in leaked.items():
        monkeypatch.setenv(name, value)

    names = ", ".join(repr(name) for name in leaked)
    result = execute_code(
        "import os\n"
        f"names = ({names})\n"
        "for name in names:\n"
        "    print(name, '=', repr(os.environ.get(name)))\n"
        "print('ENV', ' '.join(sorted(os.environ)))\n"
        "print('PATH_PRESENT', 'PATH' in os.environ)\n",
        mode="dev",
    )

    assert result.ok, result.stderr
    for value in leaked.values():
        assert value not in result.stdout, f"a value reached the transcript: {value}"
    assert result.stdout.count("None") >= len(leaked), "the variables are absent, not blank"

    environment_names = result.stdout.split("ENV ", 1)[1].splitlines()[0].split()
    forbidden = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "ACCESS_KEY")
    survivors = [name for name in environment_names if any(word in name for word in forbidden)]
    assert survivors == [], f"variables whose name carries a secret word survived: {survivors}"
    assert result.stdout.rstrip().endswith("PATH_PRESENT True"), (
        "PATH still reaches the child: the scrub is an allow-list, not an emptied environment"
    )


def test_the_snippet_cannot_read_the_secret_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVERTED on 19/11 — it used to assert the opposite, and that was the point.

    Point 0 measured, through the real interface, that a snippet read the
    operator's ``keys.toml``, ``mcp-secrets.toml``, the UHB ``.env`` and every
    ``cmdkey`` target. The audit hook installed by ``_uhb_runner.py`` now refuses
    those reads, so this test asserts the refusal with real files named by
    absolute path. A disposable ``UH_HOME`` and ``HOME`` hold the decoys — never
    the operator's own.
    """
    uh_home = tmp_path / "uh-home"
    home = tmp_path / "home"
    checkout = tmp_path / "checkout"
    (home / ".uhb").mkdir(parents=True)
    checkout.mkdir()
    uh_home.mkdir()

    decoys = {
        uh_home / "keys.toml": 'deepseek = "«redacted:sk-…»"\n',
        uh_home / "mcp-secrets.toml": "token = 'mcp-secret-decoy'\n",
        uh_home / "web-token": "WEB-TOKEN-DECOY\n",
        home / ".uhb" / "shutdown-token": "SHUTDOWN-TOKEN-DECOY\n",
        checkout / ".env": "DEEPSEEK_API_KEY=env-secret-decoy\n",
    }
    for path, content in decoys.items():
        path.write_text(content, encoding="utf-8", newline="\n")

    monkeypatch.setenv("UH_HOME", str(uh_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    code = (
        "from pathlib import Path\n"
        f"targets = {[str(path) for path in decoys]!r}\n"
        "for raw in targets:\n"
        "    path = Path(raw)\n"
        "    try:\n"
        "        print(path.name, 'READ', path.read_text(encoding='utf-8').strip())\n"
        "    except PermissionError:\n"
        "        print(path.name, 'REFUSED')\n"
        "    except OSError as exc:\n"
        "        print(path.name, 'UNREADABLE', exc.__class__.__name__)\n"
    )
    result = execute_code(code, mode="dev")

    assert result.ok, result.stderr
    for marker in ("«redacted:sk-…»", "mcp-secret-decoy", "WEB-TOKEN-DECOY",
                   "SHUTDOWN-TOKEN-DECOY", "env-secret-decoy"):
        assert marker not in result.stdout, f"{marker} reached the transcript"
    assert result.stdout.count("REFUSED") == len(decoys), result.stdout


def test_a_write_outside_the_run_directory_is_refused(tmp_path: Path) -> None:
    """The confinement the badge is allowed to describe: writes stay in the run."""
    outside = tmp_path / "escaped.txt"
    result = execute_code(
        "from pathlib import Path\n"
        f"target = Path({str(outside)!r})\n"
        "try:\n"
        "    target.write_text('escaped', encoding='utf-8')\n"
        "    print('WROTE')\n"
        "except PermissionError:\n"
        "    print('REFUSED')\n"
        "print('STILL_THERE', target.exists())\n",
        mode="dev",
    )

    assert result.ok, result.stderr
    assert "REFUSED" in result.stdout, result.stdout
    assert "WROTE" not in result.stdout, result.stdout
    assert not outside.exists(), "a refused write must not leave a file behind"


def test_a_delete_outside_the_run_directory_is_refused(tmp_path: Path) -> None:
    victim = tmp_path / "not-yours.txt"
    victim.write_text("keep me\n", encoding="utf-8")

    result = execute_code(
        "from pathlib import Path\n"
        f"target = Path({str(victim)!r})\n"
        "try:\n"
        "    target.unlink()\n"
        "    print('DELETED')\n"
        "except PermissionError:\n"
        "    print('REFUSED')\n",
        mode="dev",
    )

    assert result.ok, result.stderr
    assert "REFUSED" in result.stdout, result.stdout
    assert victim.is_file(), "a refused delete must leave the file alone"


def test_the_snippet_cannot_start_a_process(tmp_path: Path) -> None:
    """``subprocess`` was the escape route the model used on 19/11 (425/435)."""
    result = execute_code(
        "import subprocess, os, sys\n"
        "for attempt in ('subprocess', 'os.system'):\n"
        "    try:\n"
        "        if attempt == 'subprocess':\n"
        "            subprocess.run([sys.executable, '-c', 'print(1)'], check=False)\n"
        "        else:\n"
        "            os.system('cmd /c echo 1')\n"
        "        print(attempt, 'RUN')\n"
        "    except PermissionError:\n"
        "        print(attempt, 'REFUSED')\n",
        mode="dev",
    )

    assert result.ok, result.stderr
    assert result.stdout.count("REFUSED") == 2, result.stdout
    assert "RUN" not in result.stdout, result.stdout


def test_a_gui_module_cannot_be_imported() -> None:
    """The contract says "draw a file"; a window is not an artefact."""
    result = execute_code(
        "try:\n"
        "    import turtle\n"
        "    print('IMPORTED')\n"
        "except PermissionError:\n"
        "    print('REFUSED')\n",
        mode="dev",
    )

    assert result.ok, result.stderr
    assert "REFUSED" in result.stdout, result.stdout


def test_the_workshop_interpreter_is_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "atelier" / "python.exe"
    fake.parent.mkdir()
    fake.write_text("not really an interpreter\n", encoding="utf-8")
    monkeypatch.setenv(tool.ATELIER_ENV, str(fake))

    assert tool.atelier_python() == str(fake)
    assert tool._python() == str(fake)
    assert tool.execute_facts()["atelier"] is True


def test_without_a_workshop_the_host_interpreter_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No venv is a normal state: the diagnostic says which python runs, plainly."""
    monkeypatch.setenv(tool.ATELIER_ENV, str(tmp_path / "absent" / "python.exe"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UH_HOME", raising=False)

    facts = tool.execute_facts()

    assert facts["atelier"] is False
    assert facts["interpreter"] == (tool.sys.executable or "python")
    assert facts["confinement"]["writes"] == "run directory only"


def test_the_result_names_the_interpreter(tmp_path: Path) -> None:
    result = execute_code("print('ok')", mode="dev")

    assert result.ok
    assert result.interpreter == tool._python()
    assert result.to_dict()["interpreter"] == result.interpreter


def test_the_server_status_publishes_the_interpreter() -> None:
    from uhb.entrypoints.panel import status_payload

    payload = status_payload(
        version="0.0.0",
        mode="dev",
        harness="hermes",
        providers=[],
        modes=[],
        execute={"interpreter": "C:/atelier/python.exe", "atelier": True},
    )

    assert payload["execute"]["interpreter"] == "C:/atelier/python.exe"
    assert payload["execute"]["atelier"] is True


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
        f"open('figs/plot.png', 'wb').write(base64.b64decode({base64.b64encode(PNG_1X1).decode()!r}))",
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
        #: `post()` mints the approval an interface obtains after the operator
        #: clicked, so the tests that are not about approvals keep testing what
        #: they were written for. The approval tests call ``post_unapproved``.
        self.approved = True

    def serve_mode(self, mode: str) -> None:
        """The mode the next request is served under (Handler.mode is per request)."""
        self._module.Handler.mode = mode

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        if path == "/tools/execute" and self.approved:
            payload = {**payload, "approval_id": self.approval()}
        return self.post_unapproved(path, payload)

    def post_unapproved(self, path: str, payload: dict) -> tuple[int, dict]:
        """The raw call: no nonce added, whatever the path."""
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

    def approval(self, tool: str = "execute_code") -> str:
        """Mint a nonce the way an interface does, after the operator clicked."""
        status, data = self.post_unapproved("/tools/approvals", {"tool": tool})
        assert status == 200, data
        return str(data["approval_id"])

    def execute(self, code: str, **extra) -> tuple[int, dict]:
        """Approved `POST /tools/execute`, spelled out rather than implied."""
        body = {"code": code, "approval_id": self.approval(), **extra}
        return self.post_unapproved("/tools/execute", body)


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


def test_a_refusal_is_typed_and_written_to_the_journal(http_server, capsys) -> None:
    """UHB is the authority: a mode without `execute_code` gets 403, and says so.

    The refusal used to be answered without a trace: an operator reading the log
    could not tell a client that was refused from one that never called. The line
    names the mode and the reason, and never the snippet.
    """
    http_server.serve_mode("chat")

    status, data = http_server.post("/tools/execute", {"code": "print('secret-payload')"})

    assert status == 403
    assert data["error"]["type"] == "forbidden"
    assert "execute_code" in data["error"]["message"]
    journal = capsys.readouterr().out
    assert "[uhb] tools/execute refused (403)" in journal
    assert "[mode=chat]" in journal
    assert "secret-payload" not in journal, "the snippet must never reach the journal"


def test_a_body_naming_another_mode_is_refused_and_journaled(http_server, capsys) -> None:
    """`uhb run --mode chat` must not run dev work because a client asked nicely."""
    http_server.serve_mode("chat")

    status, data = http_server.post(
        "/tools/execute", {"code": "print(1)", "mode": "dev"}
    )

    assert status == 403
    assert "serves mode" in data["error"]["message"]
    journal = capsys.readouterr().out
    assert "[uhb] tools/execute refused (403)" in journal


# --------------------------------------------------------------------------- #
# P0-4: le bac à sable et la vérification d'effet
# --------------------------------------------------------------------------- #
def test_the_snippet_cannot_read_the_operator_credentials(http_server, monkeypatch) -> None:
    """The one that matters: a tool that runs Python must not inherit the keys.

    `_child_env` allow-lists the environment, so this is a property, not a wish —
    and a property is only real when a snippet has actually tried to read them.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-never-be-visible")
    monkeypatch.setenv("UHB_SHUTDOWN_TOKEN", "token-should-never-be-visible")
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute", {
        "code": (
            "import os\n"
            "for name in ('DEEPSEEK_API_KEY', 'UHB_SHUTDOWN_TOKEN', 'GH_TOKEN', 'AWS_SECRET_ACCESS_KEY'):\n"
            "    print(name, '=', repr(os.environ.get(name)))\n"
        )
    })

    assert status == 200
    assert data["ok"] is True
    assert "sk-should-never-be-visible" not in data["stdout"]
    assert "token-should-never-be-visible" not in data["stdout"]
    assert "None" in data["stdout"], "the variables are absent, not blank"


def test_the_run_directory_is_the_working_home(http_server, tmp_path: Path) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute", {
        "code": "import os, pathlib; print(pathlib.Path.cwd()); print(os.environ['HOME'])"
    })

    assert status == 200
    assert data["run_dir"] in data["stdout"], "cwd and HOME are the run directory"


def test_a_declared_effect_that_happens_is_verified(http_server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute", {
        "code": "open('out.txt','w').write('x'); print('saved')",
        "expect": {"file": "out.txt", "stdout_contains": "saved"},
    })

    assert status == 200
    assert data["ok"] is True
    assert data["verified"] is True
    assert data["post_state"]["checks"] == {"file": True, "stdout": True}


def test_a_declared_effect_that_does_not_happen_is_a_failure(http_server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute", {
        "code": "print('nothing written')",
        "expect": {"file": "missing.png"},
    })

    assert status == 200, "the code ran: this is a result, not an HTTP error"
    assert data["ok"] is False
    assert data["verified"] is False
    assert data["reason"]["code"] == "post_state_mismatch"
    assert data["reason"]["checks"] == {"file": False}


def test_without_a_declared_effect_no_claim_is_made(http_server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute", {"code": "print('hello')"})

    assert status == 200
    assert "post_state" not in data, "no expectation, no claim"
    assert "verified" not in data


def test_a_timeout_is_reported_not_hidden(http_server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post("/tools/execute",
                                    {"code": "import time; time.sleep(30)", "timeout": 1})

    assert status == 200
    assert data["ok"] is False
    assert data["timed_out"] is True


# --------------------------------------------------------------------------- #
# P0-4: l'approbation, autorite du serveur
# --------------------------------------------------------------------------- #
def test_without_an_approval_nothing_runs(http_server: _Server) -> None:
    """A client that skips the interface cannot execute, whatever it claims."""
    http_server.serve_mode("dev")

    status, data = http_server.post_unapproved(
        "/tools/execute", {"code": "print('not approved')", "approved": True}
    )

    assert status == 403
    assert data["error"]["type"] == "forbidden"
    assert data["error"]["code"] == "approval_required"
    assert data["error"]["tool"] == "execute_code"
    assert "tools/approvals" in data["error"]["message"], "the refusal says what to do"


def test_an_approval_authorises_one_run(http_server: _Server) -> None:
    http_server.serve_mode("dev")
    nonce = http_server.approval()

    first = http_server.post_unapproved(
        "/tools/execute", {"code": "print('one')", "approval_id": nonce}
    )
    second = http_server.post_unapproved(
        "/tools/execute", {"code": "print('two')", "approval_id": nonce}
    )

    assert first[0] == 200
    assert first[1]["stdout"].strip() == "one"
    assert second[0] == 403, "a nonce is consumed by the run it authorised"
    assert second[1]["error"]["code"] == "approval_required"


def test_an_expired_approval_is_refused(http_server: _Server) -> None:
    """Expiry is enforced by UHB, not by the client forgetting to retry."""
    http_server.serve_mode("dev")
    stale = http_server.approval()
    http_server._module._APPROVALS[stale] = (time.monotonic() - 1.0, "execute_code")

    status, data = http_server.post_unapproved(
        "/tools/execute", {"code": "print('late')", "approval_id": stale}
    )

    assert status == 403
    assert data["error"]["code"] == "approval_required"


def test_an_unknown_approval_is_refused(http_server: _Server) -> None:
    http_server.serve_mode("dev")

    status, data = http_server.post_unapproved(
        "/tools/execute", {"code": "print('guess')", "approval_id": "not-a-real-nonce"}
    )

    assert status == 403
    assert data["error"]["code"] == "approval_required"


def test_the_approval_route_only_mints_for_execute_code(http_server: _Server) -> None:
    status, _data = http_server.post_unapproved("/tools/approvals", {"tool": "desktop_snapshot"})

    assert status == 400


def test_an_approval_refusal_is_journaled_without_the_code(http_server, capsys) -> None:
    http_server.serve_mode("dev")

    http_server.post_unapproved(
        "/tools/execute", {"code": "print('should-not-be-logged')"}
    )

    journal = capsys.readouterr().out
    assert "[uhb] tools/execute refused (403)" in journal
    assert "approval" in journal
    assert "should-not-be-logged" not in journal, "the snippet never reaches the journal"
