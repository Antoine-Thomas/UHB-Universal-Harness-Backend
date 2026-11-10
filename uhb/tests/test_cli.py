"""The CLI (Phase 2): read-only commands, config edits, and a fast-forward-only update.

No test here starts a server, runs git, or reaches a model: the subprocess runner
is injected, the config path is a temporary file, and `serve` is monkeypatched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uhb.entrypoints import cli, settings, status as status_module, update as update_module


# --- harness ---------------------------------------------------------------


def make_runner(root: Path, *, dirty: str = "", before: str = "aaa1111",
                after: str = "bbb2222", pull_code: int = 0, pending: str = "2"):
    """A git stand-in: same argv shapes, scripted answers, no process."""
    shorts = [before, after]

    def run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
        joined = " ".join(argv)
        if "--show-toplevel" in joined:
            return 0, str(root), ""
        if "--abbrev-ref" in joined:
            return 0, "main", ""
        if "--porcelain" in joined:
            return 0, dirty, ""
        if "--short" in joined:
            return 0, shorts.pop(0) if shorts else after, ""
        if "fetch" in joined:
            return 0, "", ""
        if "rev-list" in joined:
            return 0, pending, ""
        if "pull" in joined:
            return pull_code, "", "diverged" if pull_code else ""
        raise AssertionError(f"unexpected git call: {joined}")

    return run


@pytest.fixture()
def config_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "config.yaml"
    monkeypatch.setenv(settings.CONFIG_ENV, str(path))
    return path


# --- status ----------------------------------------------------------------


def test_status_snapshot_never_carries_a_credential_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "value-that-must-not-appear")
    data = status_module.snapshot(tmp_path, runner=make_runner(tmp_path))
    rendered = status_module.render(data) + json.dumps(data)

    assert "value-that-must-not-appear" not in rendered
    assert "DEEPSEEK_API_KEY" in rendered, "the variable NAME is reported, the value is not"


def test_status_reports_key_loaded_as_a_boolean(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    data = status_module.snapshot(tmp_path, runner=make_runner(tmp_path))
    rows = {row["name"]: row for row in data["providers"]}

    assert rows["deepseek"]["key_loaded"] is True
    assert rows["anthropic"]["key_loaded"] is False
    assert rows["ollama"]["key_loaded"] is False
    assert rows["ollama"]["local"] is True


def test_status_render_names_missing_keys(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    text = status_module.render(status_module.snapshot(tmp_path, runner=make_runner(tmp_path)))

    assert "DEEPSEEK_API_KEY: MISSING" in text
    assert "local, no key needed" in text
    assert "modes         chat, dev, observe, review" in text


def test_status_json_is_valid_and_lists_the_four_modes(tmp_path: Path, capsys):
    code = cli.main(["status", "--json", "--workspace", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["modes"] == ["chat", "dev", "observe", "review"]
    assert {entry["tier"] for entry in payload["router"]["models"]} == {"eco", "free", "paid"}


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])

    assert "UHB (Universal Harness Backend)" in capsys.readouterr().out


# --- config ----------------------------------------------------------------


def test_config_list_falls_back_to_the_built_in_defaults(config_file: Path, capsys):
    assert not config_file.exists()

    code = cli.main(["config", "list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "routing.default = deepseek/deepseek-v4-pro" in out
    assert "providers.deepseek.api_key_env = DEEPSEEK_API_KEY" in out


def test_config_get_takes_a_dotted_path(config_file: Path, capsys):
    cli.main(["config", "get", "routing.default"])

    assert capsys.readouterr().out.strip() == "deepseek/deepseek-v4-pro"


def test_config_get_unknown_key_is_a_usage_error(config_file: Path, capsys):
    code = cli.main(["config", "get", "routing.nope"])

    assert code == 2
    assert "unknown configuration key" in capsys.readouterr().err


def test_config_set_writes_and_can_be_read_back(config_file: Path, capsys):
    code = cli.main(["config", "set", "routing.default", "kimi/moonshot-v1-128k"])

    assert code == 0
    assert config_file.is_file()
    assert settings.get(settings.load(), "routing.default") == "kimi/moonshot-v1-128k"
    assert "written to" in capsys.readouterr().out


def test_config_set_parses_json_values(config_file: Path):
    settings.set_value("routing.default", "deepseek/deepseek-flash")

    _data, number = settings.set_value("routing.retries", "3")
    _data, flag = settings.set_value("routing.strict", "true")

    assert number == 3 and flag is True
    assert settings.get(settings.load(), "routing.retries") == 3


def test_config_set_refuses_a_credential_key(config_file: Path, capsys):
    code = cli.main(["config", "set", "providers.deepseek.api_key", "anything"])

    assert code == 2
    assert "credential value never goes in a config file" in capsys.readouterr().err
    assert not config_file.exists(), "a refused write leaves no file behind"


def test_config_set_refuses_an_unknown_section(config_file: Path):
    with pytest.raises(settings.SettingsError) as caught:
        settings.set_value("freeform.key", "x")

    assert "only providers, routing are configurable" in str(caught.value)


# --- modes -----------------------------------------------------------------


def test_modes_list_shows_the_four_profiles(capsys):
    code = cli.main(["modes", "list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "chat" in out and "review" in out and "dev" in out and "observe" in out
    assert "read-only" in out, "a mode with no write glob says so"


def test_modes_show_prints_the_profile(config_file: Path, capsys):
    code = cli.main(["modes", "show", "review"])

    profile = json.loads(capsys.readouterr().out)
    assert code == 0
    assert profile["name"] == "review"
    assert profile["write"] == []
    assert profile["model"]["tier"] == "eco"


def test_modes_show_unknown_mode_is_a_usage_error(capsys):
    code = cli.main(["modes", "show", "architect"])

    assert code == 2
    assert "unknown mode 'architect'" in capsys.readouterr().err


# --- update ----------------------------------------------------------------


def test_update_refuses_a_dirty_tree(tmp_path: Path):
    with pytest.raises(update_module.UpdateError) as caught:
        update_module.run_update(runner=make_runner(tmp_path, dirty=" M uhb/core/memory.py"))

    assert "working tree is dirty" in str(caught.value)


def test_update_dry_run_reports_pending_commits_without_pulling(tmp_path: Path):
    runner = make_runner(tmp_path, pending="3")

    result = update_module.run_update(runner=runner, dry_run=True)

    assert result.dry_run is True
    assert result.pending == 3
    assert result.pulled is False
    assert result.before == result.after


def test_update_fast_forwards_and_proves_it(tmp_path: Path):
    result = update_module.run_update(runner=make_runner(tmp_path, before="aaa1111", after="bbb2222"))

    assert result.pulled is True
    assert (result.before, result.after) == ("aaa1111", "bbb2222")
    assert any("fast-forward only" in check for check in result.checks)
    assert any("no rewrite" in check for check in result.checks)


def test_update_refuses_a_diverged_branch(tmp_path: Path):
    with pytest.raises(update_module.UpdateError) as caught:
        update_module.run_update(runner=make_runner(tmp_path, pull_code=1))

    message = str(caught.value)
    assert "diverged" in message
    assert "will not merge or rebase" in message


def test_update_outside_a_checkout_says_so(tmp_path: Path):
    def no_git(argv: list[str], cwd: Path) -> tuple[int, str, str]:
        return 128, "", "fatal: not a git repository"

    with pytest.raises(update_module.UpdateError) as caught:
        update_module.run_update(runner=no_git)

    assert "not a git checkout" in str(caught.value)


# --- run -------------------------------------------------------------------


def test_run_wires_host_port_and_mode(monkeypatch, capsys):
    # `run` sets the handler's mode for the life of the process; a test must not
    # leak that into the next one.
    monkeypatch.setattr("uhb.entrypoints.server.Handler.mode", "chat")
    seen: dict[str, object] = {}

    def fake_serve(port: int, host: str) -> None:
        seen["port"] = port
        seen["host"] = host

    monkeypatch.setattr("uhb.entrypoints.server.serve", fake_serve)

    code = cli.main(["run", "--port", "8099", "--host", "127.0.0.1", "--mode", "dev"])

    assert code == 0
    assert seen == {"port": 8099, "host": "127.0.0.1"}
    banner = capsys.readouterr().out
    assert "UHB 0.1.0" in banner
    assert "mode      dev" in banner
    assert "http://127.0.0.1:8099/" in banner
    assert "http://127.0.0.1:8099/docs/manuel.pdf" in banner


def test_run_refuses_an_unknown_mode_before_opening_a_socket(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr("uhb.entrypoints.server.serve", lambda **_: called.append("served"))

    code = cli.main(["run", "--mode", "architect"])

    assert code == 2
    assert called == []


# --- the panel, the status endpoint and the embedded manual ----------------

import http.client as _http
import threading as _threading


@pytest.fixture(scope="module")
def panel_server():
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.mode
    server_module.Handler.mode = "chat"
    httpd = server_module.make_server(0, "127.0.0.1")
    _threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.mode = original


def _get(port: int, path: str, headers: dict[str, str] | None = None,
         method: str = "GET") -> tuple[int, dict, bytes]:
    conn = _http.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    headers = {key.lower(): value for key, value in response.getheaders()}
    body = response.read()
    conn.close()
    return response.status, headers, body


def test_status_endpoint_answers_with_the_instance_facts(panel_server):
    status, headers, body = _get(panel_server, "/status")

    payload = json.loads(body)
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    assert payload["connected"] is True
    assert payload["version"] == "0.1.0"
    assert payload["mode"] == "chat"
    assert payload["docs"]["manual"] == "/docs/manuel.pdf"
    assert payload["modes"] == ["chat", "dev", "observe", "review"]
    assert {row["name"] for row in payload["providers"]} >= {"deepseek", "ollama"}


def test_panel_page_carries_the_badge_and_the_manual_link(panel_server):
    status, headers, body = _get(panel_server, "/")
    page = body.decode("utf-8")

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert 'id="uhb-badge"' in page
    assert "9+ UHB" in page
    assert 'data-uhb-base=""' in page, "the panel is same-origin: no base to prefix"
    assert 'href="/docs/manuel.pdf"' in page
    assert 'href="/docs/manual.pdf"' in page, "the English manual has its own link"
    assert "uhb status" in page, "the modal lists the commands"
    assert "http://127.0.0.1" not in page.replace("http://127.0.0.1:0", ""), "no hard-coded host"
    assert "https://" not in page, "no external asset"


def test_manual_is_served_as_a_real_pdf_download(panel_server):
    status, headers, body = _get(panel_server, "/docs/manuel.pdf")

    assert status == 200
    assert headers["content-type"] == "application/pdf"
    assert headers["content-disposition"].startswith("attachment;")
    assert "MANUEL_UHB.pdf" in headers["content-disposition"]
    assert body.startswith(b"%PDF-"), "a real PDF, not an error page"
    assert len(body) > 20_000
    assert int(headers["content-length"]) == len(body)


def test_unknown_get_path_is_a_clean_404(panel_server):
    status, _headers, body = _get(panel_server, "/nope")

    assert status == 404
    assert json.loads(body)["error"]["type"] == "not_found"


def test_uhb_docs_reports_the_path_and_the_url(capsys):
    code = cli.main(["docs", "--json", "--port", "9"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["installed"] is True
    assert payload["size"] > 20_000
    assert payload["manual"].endswith("MANUEL_UHB.pdf")
    assert payload["url"] == "http://127.0.0.1:9/docs/manuel.pdf"
    assert payload["english"]["installed"] is True
    assert payload["english"]["size"] > 20_000
    assert payload["server"] is False, "nothing listens on port 9"


def test_uhb_docs_sees_a_running_server(capsys, panel_server):
    code = cli.main(["docs", "--json", "--port", str(panel_server)])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["server"] is True


def test_the_english_manual_is_served_on_its_own_path(panel_server):
    status, headers, body = _get(panel_server, "/docs/manual.pdf")

    assert status == 200
    assert headers["content-type"] == "application/pdf"
    assert body.startswith(b"%PDF-")
    assert len(body) > 20_000
    french = _get(panel_server, "/docs/manuel.pdf")[2]
    assert len(body) != len(french), "two distinct documents, not one file served twice"


# --- CORS: the widget must work from a dashboard on another port -------------


def test_status_is_readable_from_a_loopback_dashboard(panel_server):
    status, headers, _body = _get(panel_server, "/status",
                                  {"Origin": "http://127.0.0.1:8080"})

    assert status == 200
    assert headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert headers["vary"] == "Origin"


def test_status_does_not_open_to_an_arbitrary_website(panel_server):
    _status, headers, _body = _get(panel_server, "/status", {"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in headers, "no `*`, no echo: local only"


def test_a_page_opened_from_disk_may_read_status(panel_server):
    _status, headers, _body = _get(panel_server, "/status", {"Origin": "null"})

    assert headers["access-control-allow-origin"] == "null"


def test_preflight_answers_the_widget(panel_server):
    status, headers, body = _get(panel_server, "/status",
                                 {"Origin": "http://localhost:8080",
                                  "Access-Control-Request-Method": "GET"}, method="OPTIONS")

    assert status == 204
    assert headers["access-control-allow-origin"] == "http://localhost:8080"
    assert "GET" in headers["access-control-allow-methods"]
    assert body == b""


# --- uhb ui ----------------------------------------------------------------


def test_ui_points_at_the_guide_and_the_status_url(capsys):
    code = cli.main(["ui", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["guide"].endswith("UI_INTEGRATION.md")
    assert payload["guide_installed"] is True
    assert payload["status_url"] == "http://127.0.0.1:8101/status"
    assert payload["markup_chars"] > 4000


def test_ui_markup_is_a_self_contained_widget(capsys):
    code = cli.main(["ui", "--markup", "--base", "http://127.0.0.1:9000"])
    markup = capsys.readouterr().out

    assert code == 0
    assert 'id="uhb-badge"' in markup
    assert 'id="uhb-modal"' in markup
    assert 'data-uhb-base="http://127.0.0.1:9000"' in markup
    assert "9+ UHB" in markup
    assert 'fetch(base + "/status"' in markup
    assert "<main>" not in markup, "no page markup in the snippet"
    assert "body {" not in markup, "no page CSS in the snippet: it would reset the host"
    assert "https://" not in markup, "no external asset"
