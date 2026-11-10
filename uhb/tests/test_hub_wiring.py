"""Modes × Router wiring (Phase 1.4): the pipeline asks the policy, not a constant.

Three seams, three proofs:

- ``routed_llm`` turns a prompt into a routed call, so complexity chooses the model
- ``ingest`` with a mode uses that seam instead of a hard-coded endpoint
- the entrypoints route when the caller names no model, and refuse an unknown mode

No test here reaches the network: the sender is injected or monkeypatched.
"""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import threading
from pathlib import Path

import pytest

from uhb.core.types import StreamEvent
from uhb.entrypoints import fake_claude
from uhb.hub.modes import ModeRequired, clear_cache as clear_modes
from uhb.hub.router import AllProvidersFailed, clear_cache, route, routed_llm, select_model
from uhb.hub.wiki.scripts import DEFAULT_ROOT
from uhb.hub.wiki.scripts.ingest import ingest

TRIVIAL = "Quel est l'etat du disque ?"
COMPLEX = (
    "Refactor, migrate and rewrite the executor, debug the streaming path in "
    "uhb/core/executor.py and uhb/core/router.py, then add tests and document it."
)

PAGE = {
    "slug": "hub",
    "title": "HUB",
    "tags": ["concept"],
    "links": ["uhb"],
    "body": "# HUB\n\nThe brain. See [[uhb]] and [[wiki-pattern]].",
}


@pytest.fixture(autouse=True)
def _fresh():
    clear_cache()
    clear_modes()
    yield
    clear_cache()
    clear_modes()


class Recorder:
    """A sender that records the candidate it was asked to call."""

    def __init__(self, reply: str = "{}", fail: set[str] | None = None) -> None:
        self.reply = reply
        self.fail = set(fail or ())
        self.candidates: list[str] = []

    def __call__(self, candidate, prompt: str, max_tokens: int) -> str:
        self.candidates.append(candidate.ref)
        if candidate.ref in self.fail:
            raise TimeoutError("provider did not answer")
        return self.reply


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    wiki = root / "uhb" / "hub" / "wiki"
    for rel in ("raw/notes", "wiki/concepts", "wiki/entities", "wiki/procedures", "wiki/references"):
        (wiki / rel).mkdir(parents=True, exist_ok=True)
    for name in ("schema.md", "index.md", "log.md", "contradictions.md"):
        source = DEFAULT_ROOT / "wiki" / name
        (wiki / "wiki" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def wiki_root(repo: Path) -> Path:
    return repo / "uhb" / "hub" / "wiki"


def add_source(repo: Path, text: str = "The HUB compiles sources into pages.") -> Path:
    path = wiki_root(repo) / "raw" / "notes" / "hub.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- the seam --------------------------------------------------------------


def test_routed_llm_sends_a_trivial_prompt_to_the_cheap_model():
    recorder = Recorder(reply="ok")

    text = routed_llm("dev", sender=recorder)(TRIVIAL)

    assert text == "ok"
    assert recorder.candidates == ["deepseek/deepseek-flash"]


def test_routed_llm_sends_a_complex_prompt_to_the_capable_model():
    recorder = Recorder(reply="ok")

    routed_llm("dev", sender=recorder)(COMPLEX)

    assert recorder.candidates == ["deepseek/deepseek-v4-pro"], (
        "flash does not claim 'complex': the capable model answers first"
    )


def test_select_model_prefers_an_explicit_choice_and_routes_otherwise():
    assert select_model(TRIVIAL, "dev", "deepseek/deepseek-v4-pro") == "deepseek/deepseek-v4-pro"
    assert select_model(TRIVIAL, "dev") == route(TRIVIAL, "dev").chosen.ref


def test_routed_llm_falls_back_down_the_chain():
    recorder = Recorder(reply="second", fail={"deepseek/deepseek-flash"})

    text = routed_llm("dev", sender=recorder)(TRIVIAL)

    assert text == "second"
    assert recorder.candidates == [
        "deepseek/deepseek-flash",
        "deepseek/deepseek-v4-pro",
    ]


# --- the pipeline uses the seam -------------------------------------------


def test_ingest_asks_the_router_and_honours_its_choice(repo: Path, monkeypatch):
    """With a mode and no injected LLM, the compile call goes through the router."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-never-logged")
    recorder = Recorder(reply=json.dumps({"pages": [PAGE]}))
    monkeypatch.setattr("uhb.hub.router.default_sender", recorder)
    source = add_source(repo)

    result = ingest(source, workspace=wiki_root(repo), project=repo, mode="dev")

    assert result.pages_created == ["concepts/hub.md"]
    assert recorder.candidates == ["deepseek/deepseek-flash"], (
        "a compile is routed as a standard task: the cheap eco model answers first"
    )
    assert (wiki_root(repo) / "wiki" / "concepts" / "hub.md").is_file()


def test_ingest_falls_back_to_the_next_model_when_the_first_fails(repo: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-never-logged")
    recorder = Recorder(reply=json.dumps({"pages": [PAGE]}), fail={"deepseek/deepseek-flash"})
    monkeypatch.setattr("uhb.hub.router.default_sender", recorder)

    result = ingest(add_source(repo), workspace=wiki_root(repo), project=repo, mode="dev")

    assert result.pages_created == ["concepts/hub.md"]
    assert recorder.candidates == [
        "deepseek/deepseek-flash",
        "deepseek/deepseek-v4-pro",
    ]


def test_ingest_writes_nothing_when_every_candidate_fails(repo: Path, monkeypatch):
    """Fail-closed through the pipeline: a routed compile that fails leaves no page."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-never-logged")
    recorder = Recorder(
        reply=json.dumps({"pages": [PAGE]}),
        fail={
            "deepseek/deepseek-flash",
            "deepseek/deepseek-v4-pro",
            "ollama/qwen3:8b",
        },
    )
    monkeypatch.setattr("uhb.hub.router.default_sender", recorder)

    with pytest.raises(AllProvidersFailed) as caught:
        ingest(add_source(repo), workspace=wiki_root(repo), project=repo, mode="dev")

    assert [attempt.outcome for attempt in caught.value.attempts] == ["failed", "failed", "failed"]
    assert not (wiki_root(repo) / "wiki" / "concepts" / "hub.md").exists()
    assert "[[hub]]" not in (wiki_root(repo) / "wiki" / "index.md").read_text(encoding="utf-8")


def test_ingest_without_a_mode_fails_closed(repo: Path, monkeypatch):
    """No mode = no envelope: the write path refuses instead of assuming one."""
    called: list[str] = []
    monkeypatch.setattr(
        "uhb.hub.wiki.scripts.ingest.default_llm", lambda _p: called.append("plain") or "{}"
    )

    with pytest.raises(ModeRequired) as caught:
        ingest(add_source(repo), workspace=wiki_root(repo), project=repo)

    assert "ingest() needs a mode" in str(caught.value)
    assert called == [], "the refusal happens before the model call"
    assert not (wiki_root(repo) / "wiki" / "concepts" / "hub.md").exists()


# --- entrypoints -----------------------------------------------------------


class CapturingAdapter:
    """Yields one token and records the TaskContext it was handed."""

    def __init__(self) -> None:
        self.context = None

    async def execute_task(self, prompt, context):
        self.context = context
        yield StreamEvent("token", "hello")


def run_fake_claude(adapter, argv: list[str], prompt: str = "hi") -> tuple[int, list[dict]]:
    args = fake_claude.build_parser().parse_args(argv)
    source = io.StringIO(
        json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}) + "\n"
    )
    out = io.StringIO()
    code = asyncio.run(fake_claude.run(args, adapter=adapter, source=source, out=out))
    return code, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_fake_claude_routes_when_no_model_is_given():
    adapter = CapturingAdapter()

    code, lines = run_fake_claude(adapter, ["-p", "--mode", "dev", "--session-id", "s1"], prompt=TRIVIAL)

    assert code == 0
    assert lines[0]["mode"] == "dev"
    assert lines[0]["model"] == route(TRIVIAL, "dev").chosen.ref
    assert adapter.context.model_ref == lines[0]["model"]
    assert adapter.context.metadata["mode"] == "dev"


def test_fake_claude_keeps_an_explicit_model():
    adapter = CapturingAdapter()

    code, lines = run_fake_claude(adapter, ["-p", "--mode", "dev", "--model", "deepseek/deepseek-v4-pro"])

    assert code == 0
    assert lines[0]["model"] == "deepseek/deepseek-v4-pro"


def test_fake_claude_refuses_an_unknown_mode():
    adapter = CapturingAdapter()

    code, lines = run_fake_claude(adapter, ["-p", "--mode", "architect"])

    assert code == 1
    assert adapter.context is None, "no turn is started"
    assert lines[-1]["is_error"] is True
    assert "architect" in lines[-2]["message"]["content"][0]["text"]


@pytest.fixture(scope="module")
def server():
    from uhb.entrypoints import server as server_module

    adapter = CapturingAdapter()
    original = server_module.Handler.adapter
    server_module.Handler.adapter = adapter
    httpd = server_module.make_server(0, "127.0.0.1")
    # poll_interval is the shutdown latency: 0.5 s by default, 10 ms here.
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        yield httpd.server_address[1], adapter
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.adapter = original


def post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    return response.status, data


def test_server_routes_by_mode_when_no_model_is_named(server):
    port, adapter = server

    status, data = post(
        port, "/v1/chat/completions", {"model": "", "mode": "dev", "messages": [{"role": "user", "content": COMPLEX}]}
    )

    assert status == 200
    assert data["model"] == route(COMPLEX, "dev").chosen.ref
    assert adapter.context.metadata["mode"] == "dev"


def test_server_rejects_an_unknown_mode(server):
    port, adapter = server
    adapter.context = None  # the previous test shared this server

    status, data = post(port, "/v1/chat/completions", {"mode": "architect", "messages": [{"role": "user", "content": "hi"}]})

    assert status == 400
    assert "architect" in data["error"]["message"]
    assert adapter.context is None
