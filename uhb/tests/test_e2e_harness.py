"""End-to-end: a real subprocess harness behind the real HTTP and CLI entrypoints.

Two things were never proven before this file: that `uhb run` / `uhb execute`
actually talk to a harness process, and that the routed model reaches it. The
harness here is a real subprocess (a Python script the adapter launches); it is
not a mock of the adapter — only of the *product* behind it.

The last test proves the DeepSeek path without a key: an httpx transport stands in
for the vendor, so the assertions are about the request UHB sends (URL, model id,
reasoning flag) and about the pipeline accepting an OpenAI-shaped reply.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest

from uhb.adapters.hermes import HermesAdapter
from uhb.core.router import Router
from uhb.entrypoints import cli
from uhb.hub.router import AllProvidersFailed, clear_cache, route
from uhb.hub.wiki.scripts import DEFAULT_ROOT, split_frontmatter
from uhb.hub.wiki.scripts.ingest import ingest
from uhb.hub.modes import clear_cache as clear_modes

STUB = """import json, os, sys
path = os.environ.get("UHB_STUB_ARGV_LOG")
if path:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sys.argv[1:], fh)
for line in ("hello ", "from ", "the stub"):
    print(line, flush=True)
"""

STUB_FAILS = """import sys
sys.stderr.write("harness exploded\\n")
sys.exit(3)
"""


@pytest.fixture(autouse=True)
def _fresh():
    clear_cache()
    clear_modes()
    yield
    clear_cache()
    clear_modes()


@pytest.fixture()
def stub(tmp_path: Path, monkeypatch) -> tuple[list[str], Path]:
    """A real executable harness: `python stub.py -p profile -z prompt ...`."""
    script = tmp_path / "stub_harness.py"
    script.write_text(STUB, encoding="utf-8")
    log = tmp_path / "argv.json"
    monkeypatch.setenv("UHB_STUB_ARGV_LOG", str(log))
    return [sys.executable, str(script)], log


def post(port: int, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request(
        "POST", "/v1/chat/completions",
        body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
    )
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    return response.status, data


# --- point 1: `uhb run` with a harness behind it ---------------------------


def test_http_entrypoint_runs_a_real_harness_and_returns_its_tokens(stub):
    binary, log = stub
    from uhb.entrypoints import server as server_module

    adapter = HermesAdapter(binary=binary, mode="dev", router=Router())
    original = server_module.Handler.adapter
    server_module.Handler.adapter = adapter
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        status, data = post(
            httpd.server_address[1],
            {"model": "", "mode": "dev", "messages": [{"role": "user", "content": "dis bonjour"}]},
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.adapter = original

    assert status == 200
    assert data["choices"][0]["message"]["content"] == "hello from the stub"
    assert data["model"] == route("dis bonjour", "dev").chosen.ref

    argv = json.loads(log.read_text(encoding="utf-8"))
    assert "-z" in argv and "dis bonjour" in argv, "the prompt reached the harness process"
    assert argv[argv.index("--provider") + 1] == "deepseek"
    assert argv[argv.index("-m") + 1] == "deepseek-flash", "the routed model reached the harness"


# --- point 2: `uhb execute` with a harness behind it -----------------------


def test_cli_execute_runs_a_real_harness(stub, monkeypatch, capsys):
    binary, log = stub
    monkeypatch.setattr(
        "uhb.entrypoints.cli.HermesAdapter",
        lambda **kwargs: HermesAdapter(binary=binary, router=Router(), **kwargs),
    )

    code = cli.main(["execute", "dis bonjour", "--mode", "chat"])

    out = capsys.readouterr().out
    assert code == 0
    assert "hello from the stub" in out.replace("\n", "")

    argv = json.loads(log.read_text(encoding="utf-8"))
    assert argv[argv.index("-p") + 1] == "default"
    assert argv[argv.index("--provider") + 1] == route("dis bonjour", "chat").chosen.ref.split("/")[0]
    assert argv[argv.index("-m") + 1] == route("dis bonjour", "chat").chosen.model_id


def test_cli_execute_reports_a_failing_harness(tmp_path: Path, monkeypatch, capsys):
    script = tmp_path / "broken_harness.py"
    script.write_text(STUB_FAILS, encoding="utf-8")
    monkeypatch.setattr(
        "uhb.entrypoints.cli.HermesAdapter",
        lambda **kwargs: HermesAdapter(binary=[sys.executable, str(script)], router=Router(), **kwargs),
    )

    code = cli.main(["execute", "dis bonjour", "--mode", "chat"])

    captured = capsys.readouterr()
    assert code == 1
    assert "harness exploded" in captured.err


# --- point 3: the DeepSeek path, proven without a key ---------------------


REPLY = {
    "id": "mock-1",
    "object": "chat.completion",
    "model": "deepseek-flash",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "pages": [
                            {
                                "slug": "hub",
                                "title": "HUB",
                                "tags": ["concept"],
                                "links": ["wiki-pattern"],
                                "body": "# HUB\n\nThe brain. See [[wiki-pattern]] and [[ingest]].",
                            }
                        ]
                    }
                ),
            },
            "finish_reason": "stop",
        }
    ],
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    wiki = root / "uhb" / "hub" / "wiki"
    for rel in ("raw/notes", "wiki/concepts", "wiki/entities", "wiki/procedures", "wiki/references"):
        (wiki / rel).mkdir(parents=True, exist_ok=True)
    for name in ("schema.md", "index.md", "log.md", "contradictions.md"):
        (wiki / "wiki" / name).write_text((DEFAULT_ROOT / "wiki" / name).read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_the_router_calls_deepseek_flash_with_the_documented_request(repo: Path, monkeypatch):
    """No key needed: the transport is mocked, the router and the pipeline are real."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-never-logged")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "url": str(request.url),
                "model": json.loads(request.content)["model"],
                "reasoning": json.loads(request.content).get("reasoning_effort"),
                "auth": request.headers.get("authorization", "").startswith("Bearer "),
            }
        )
        return httpx.Response(200, json=REPLY)

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("httpx.Client", factory)

    source = repo / "uhb" / "hub" / "wiki" / "raw" / "notes" / "note.md"
    source.write_text("The HUB compiles sources into pages.\n", encoding="utf-8")
    result = ingest(source, workspace=repo / "uhb" / "hub" / "wiki", project=repo, mode="dev")

    assert result.pages_created == ["concepts/hub.md"]
    assert seen == [
        {
            "url": "https://api.deepseek.com/v1/chat/completions",
            "model": "deepseek-flash",
            "reasoning": "none",
            "auth": True,
        }
    ]
    meta, body = split_frontmatter(
        (repo / "uhb" / "hub" / "wiki" / "wiki" / "concepts" / "hub.md").read_text(encoding="utf-8")
    )
    assert meta["sources"] == ["raw/notes/note.md"]
    assert "[[wiki-pattern]]" in body


def test_a_vendor_error_falls_through_the_chain(repo: Path, monkeypatch):
    """A 500 from every provider is a recorded chain, not an empty wiki page."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-never-logged")
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.host, json.loads(request.content)["model"]))
        return httpx.Response(500, json={"error": {"message": "upstream down"}})

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("httpx.Client", factory)

    source = repo / "uhb" / "hub" / "wiki" / "raw" / "notes" / "note.md"
    source.write_text("The HUB compiles sources.\n", encoding="utf-8")

    with pytest.raises(AllProvidersFailed) as caught:
        ingest(source, workspace=repo / "uhb" / "hub" / "wiki", project=repo, mode="dev")

    assert [model for _host, model in calls] == ["deepseek-flash", "deepseek-v4-pro", "qwen3:8b"]
    assert [attempt.outcome for attempt in caught.value.attempts] == ["failed", "failed", "failed"]
    assert not (repo / "uhb" / "hub" / "wiki" / "wiki" / "concepts" / "hub.md").exists()
