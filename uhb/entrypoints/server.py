"""HTTP entrypoint: Anthropic ``/v1/messages`` and OpenAI ``/v1/chat/completions``.

This is the shape UHB takes for a control plane that only knows how to call an
API — OpenClaw's CLI backend can point here, and so can any product that sets a
base URL. Loopback only by default: a translator is an execution surface, and
``0.0.0.0`` is a mistake, not a convenience.

Loopback is not a boundary against the browser. Any page the user has open can
POST to ``127.0.0.1`` without a preflight (a ``text/plain`` body is a simple
request), so a turn could be spent on someone else's page: the response stays
unreadable cross-origin, the tokens do not. Two checks close that: a POST whose
``Origin`` is neither loopback nor ``null`` is refused with 403, and a POST whose
``Content-Type`` is not ``application/json`` is refused with 415. A request with
no ``Origin`` at all is a non-browser client — curl, a harness — and is allowed,
because the policy is loopback-first, not browser-first.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .. import __version__
from ..adapters.hermes import HermesAdapter
from ..core.executor import Executor
from ..core.health import Health
from ..core.router import Router
from ..core.types import StreamEvent, TaskContext
from ..hub.modes import UnknownMode, get_mode, load_modes
from ..hub.router import select_model
from .panel import manual_path, render as render_panel, status_payload
from .status import providers_facts

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
    return ""


def _run_turn(
    adapter, prompt: str, model_ref: str, workspace: str, mode_name: str = ""
) -> list[StreamEvent]:
    executor = Executor(adapter)

    async def collect() -> list[StreamEvent]:
        events: list[StreamEvent] = []
        async for event in executor.execute_task(
            prompt,
            TaskContext(workspace=workspace, model_ref=model_ref, metadata={"mode": mode_name}),
        ):
            events.append(event)
        return events

    return asyncio.run(collect())


#: Only these origins may read `/status` from a browser. A dashboard on another
#: loopback port (the common case) and a page opened from disk (`null`) qualify;
#: any other website the user visits does not get the header at all.
LOOPBACK_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


class Handler(BaseHTTPRequestHandler):
    adapter = HermesAdapter()
    workspace = ""
    #: The envelope this instance serves. `uhb run --mode` sets it; `chat` is the
    #: read-only default, so a server started without a mode cannot write.
    mode = "chat"

    server_version = "uhb/0.1"

    # -- routing --------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/health", "/healthz"):
            checks = asyncio.run(Health(workspace=self.workspace or None).health()).checks
            self._json(200, {"status": "ok", "checks": checks})
            return
        if path == "/v1/models":
            router = Router.from_config()
            self._json(200, {"object": "list", "data": [
                {"id": name, "object": "model"} for name in sorted(router.providers)
            ]})
            return
        if path in ("/", "/index.html"):
            self._html(200, render_panel(
                version=__version__, mode=self.mode,
                harness=getattr(self.adapter, "name", type(self.adapter).__name__),
            ))
            return
        if path == "/status":
            self._json(200, status_payload(
                version=__version__,
                mode=self.mode,
                harness=getattr(self.adapter, "name", type(self.adapter).__name__),
                providers=providers_facts(Router.from_config()),
                modes=sorted(load_modes()),
            ))
            return
        if path == "/docs/manuel.pdf":
            self._manual("fr")
            return
        if path == "/docs/manual.pdf":
            self._manual("en")
            return
        self._json(404, {"error": {"message": "not found", "type": "not_found"}})

    def _manual(self, lang: str = "fr") -> None:
        """Serve an embedded manual as a download, or say precisely why it is absent."""
        path = manual_path(lang)
        if lang == "en" and not path.is_file():  # never 404 when only one language shipped
            path = manual_path("fr")
        if not path.is_file():
            self._json(404, {"error": {
                "message": "the manual is not installed with this package: "
                           "reinstall with `pip install -e .` from a checkout, or read "
                           "the markdown next to it",
                "type": "not_found"}})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="MANUEL_UHB.pdf"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, markup: str) -> None:
        body = markup.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        if path not in ("/v1/chat/completions", "/v1/messages"):
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        # Refuse before reading a byte of the body: a foreign page cannot spend
        # the operator's tokens, and a non-JSON POST is not a client we serve.
        if self._origin_refused():
            self._json(403, {"error": {"message": "cross-origin POST refused",
                                       "type": "forbidden"}})
            return
        if not self._is_json():
            self._json(415, {"error": {"message": "Content-Type must be application/json",
                                       "type": "unsupported_media_type"}})
            return
        payload = self._body()
        if payload is None:
            return
        if path == "/v1/chat/completions":
            self._openai(payload)
        else:
            self._anthropic(payload)

    # -- providers ------------------------------------------------------------

    def _target(self, payload: dict[str, Any], prompt: str) -> tuple[str, str] | None:
        """``(model_ref, mode_name)``; sends a 400 and returns None on a bad mode.

        An explicit ``model`` in the payload wins. Otherwise the mode decides,
        through the router — the caller never names a model the policy did not
        choose.
        """
        mode_name = str(payload.get("mode") or "").strip()
        model = str(payload.get("model") or "")
        if not mode_name:
            return model, ""
        try:
            spec = get_mode(mode_name)
        except UnknownMode as exc:
            self._json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return None
        return select_model(prompt, spec, model), mode_name

    def _openai(self, payload: dict[str, Any]) -> None:
        prompt = _last_user_text(payload.get("messages", []))
        target = self._target(payload, prompt)
        if target is None:
            return
        model, mode_name = target
        if payload.get("stream"):
            self._sse_headers()
            for event in _run_turn(self.adapter, prompt, model, self.workspace, mode_name):
                if event.type == "done":
                    self.wfile.write(b"data: [DONE]\n\n")
                    continue
                chunk = {"id": "uhb-" + str(int(time.time())), "object": "chat.completion.chunk",
                         "model": model, "choices": [{"index": 0, "delta": {"content": event.content},
                                                      "finish_reason": None}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            return
        events = _run_turn(self.adapter, prompt, model, self.workspace, mode_name)
        text = "".join(e.content for e in events if e.type == "token")
        self._json(200, {
            "id": "uhb-" + str(int(time.time())), "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _anthropic(self, payload: dict[str, Any]) -> None:
        prompt = _last_user_text(payload.get("messages", []))
        target = self._target(payload, prompt)
        if target is None:
            return
        model, mode_name = target
        if payload.get("stream"):
            self._sse_headers()
            self.wfile.write(b"event: message_start\ndata: " + json.dumps(
                {"type": "message_start", "message": {"id": "uhb", "role": "assistant",
                                                      "content": [], "model": model}}).encode() + b"\n\n")
            for event in _run_turn(self.adapter, prompt, model, self.workspace, mode_name):
                if event.type != "token" or not event.content:
                    continue
                delta = {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": event.content}}
                self.wfile.write(b"event: content_block_delta\ndata: " + json.dumps(delta).encode() + b"\n\n")
            self.wfile.write(b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
            self.wfile.flush()
            return
        events = _run_turn(self.adapter, prompt, model, self.workspace, mode_name)
        text = "".join(e.content for e in events if e.type == "token")
        self._json(200, {"id": "uhb", "type": "message", "role": "assistant", "model": model,
                         "content": [{"type": "text", "text": text}],
                         "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0}})

    # -- plumbing -------------------------------------------------------------

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": {"message": "invalid JSON body", "type": "invalid_request"}})
            return None

    def _origin_refused(self) -> bool:
        """A POST from a foreign page is refused; no Origin header means a client.

        Browsers send ``Origin`` on every cross-origin POST, including the
        ``text/plain`` simple requests that skip the preflight. curl, a harness
        and an SDK send none — they are the intended callers.
        """
        origin = self.headers.get("Origin", "")
        if not origin:
            return False
        return origin != "null" and not LOOPBACK_ORIGIN.match(origin)

    def _is_json(self) -> bool:
        """``application/json`` optionally followed by parameters; nothing else."""
        media = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        return media == "application/json"

    def _cors_origin(self) -> str:
        """Echo the Origin only when it is local. Never `*`: `/status` is readable
        by any page the user happens to have open, and it names their providers."""
        origin = self.headers.get("Origin", "")
        if origin == "null" or LOOPBACK_ORIGIN.match(origin):
            return origin
        return ""

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Preflight for the embedded widget, read-only and local-only."""
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "accept, content-type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # never log headers or bodies
        return None


def make_server(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def serve(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> None:
    server = make_server(port, host)
    print(f"uhb server on http://{host}:{port} (loopback by default)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
