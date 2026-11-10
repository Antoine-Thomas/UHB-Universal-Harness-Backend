"""Entrypoints: the fake ``claude`` CLI and the HTTP server, with no network."""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import threading

import pytest

from uhb.core.types import StreamEvent
from uhb.entrypoints import fake_claude


class FakeAdapter:
    """Yields a fixed turn. No subprocess, no HTTP, no model."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def execute_task(self, prompt, context):
        if self.fail:
            yield StreamEvent("error", "boom", {"code": "Boom"})
            return
        yield StreamEvent("token", "hello ")
        yield StreamEvent("token", "world")


def _run_cli(adapter, argv=("-p", "--model", "deepseek/x", "--session-id", "s1"), stdin=None):
    args = fake_claude.build_parser().parse_args(list(argv))
    source = io.StringIO(stdin or '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}\n')
    out = io.StringIO()
    code = asyncio.run(fake_claude.run(args, adapter=adapter, source=source, out=out))
    return code, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_fake_claude_emits_init_assistant_and_result():
    code, lines = _run_cli(FakeAdapter())
    assert code == 0
    assert lines[0]["type"] == "system" and lines[0]["subtype"] == "init"
    assert lines[-1]["type"] == "result" and lines[-1]["is_error"] is False
    text = "".join(
        block["text"]
        for line in lines
        if line["type"] == "assistant"
        for block in line["message"]["content"]
    )
    assert text == "hello world"


def test_fake_claude_returns_one_on_error():
    code, lines = _run_cli(FakeAdapter(fail=True))
    assert code == 1
    assert lines[-1]["is_error"] is True


def test_fake_claude_echoes_the_session_id():
    _code, lines = _run_cli(FakeAdapter(), argv=("-p", "--session-id", "abc123"))
    assert all(line.get("session_id") == "abc123" for line in lines)


def test_read_prompt_from_stream_json():
    source = io.StringIO('{"type":"user","message":{"role":"user","content":[{"type":"text","text":"fix it"}]}}\n')
    assert fake_claude.read_prompt(source) == "fix it"


def test_read_prompt_falls_back_to_plain_text():
    assert fake_claude.read_prompt(io.StringIO("just text\n")) == "just text"


def test_emit_writes_one_json_line_per_event():
    out = io.StringIO()
    fake_claude.emit(out, {"type": "x"})
    assert out.getvalue() == '{"type": "x"}\n'


@pytest.fixture()
def server():
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.adapter
    server_module.Handler.adapter = FakeAdapter()
    httpd = server_module.make_server(0, "127.0.0.1")
    # poll_interval is the shutdown latency (0.5 s by default, 10 ms here).
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.adapter = original


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode()
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    return response.status, data


def test_server_answers_openai_chat_completions(server):
    status, data = _post(server, "/v1/chat/completions",
                         {"model": "deepseek/x", "messages": [{"role": "user", "content": "hi"}]})
    assert status == 200
    assert data["choices"][0]["message"]["content"] == "hello world"


def test_server_answers_anthropic_messages(server):
    status, data = _post(server, "/v1/messages",
                         {"model": "deepseek/x", "messages": [{"role": "user", "content": "hi"}]})
    assert status == 200
    assert data["type"] == "message"
    assert data["content"][0]["text"] == "hello world"


def test_server_lists_models():
    from uhb.entrypoints import server as server_module

    original = server_module.Handler.adapter
    server_module.Handler.adapter = FakeAdapter()
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", "/v1/models")
        data = json.loads(conn.getresponse().read().decode())
        conn.close()
        assert data["object"] == "list"
        assert data["data"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.Handler.adapter = original


def test_server_rejects_invalid_json(server):
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request("POST", "/v1/chat/completions", body=b"{not json",
                 headers={"Content-Type": "application/json"})
    assert conn.getresponse().status == 400
    conn.close()
