"""HTTP entrypoint: Anthropic ``/v1/messages`` and OpenAI ``/v1/chat/completions``.

This is the shape UHB takes for a control plane that only knows how to call an
API — OpenClaw's CLI backend can point here, and so can any product that sets a
base URL. Loopback only by default: a translator is an execution surface, and
``0.0.0.0`` is a mistake, not a convenience.

Two routes let a client decide *before* it spends: ``GET /modes`` says what each
mode may read, write and call, and ``POST /route`` returns the plan — the chain
of models and the reason for it — without running a turn. Neither touches an
adapter, so both are safe to call on every keystroke.

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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .. import __version__
from ..adapters.hermes import HermesAdapter
from ..core.executor import Executor
from ..core.health import Health
from ..core.router import Router
from ..core.types import StreamEvent, TaskContext
from ..hub.modes import (
    ModeRequired,
    ModesError,
    ModeViolation,
    UnknownMode,
    get_mode,
    load_modes,
)
from ..hub.router import (
    COMPLEXITIES,
    InvalidCatalog,
    UnroutableRequest,
    route,
    select_model,
)
from ..tools import catalog, desktop
from ..tools.execute import DEFAULT_TIMEOUT as TOOL_DEFAULT_TIMEOUT
from ..tools.execute import CodeExecutionError, execute_code
from . import memory_routes
from . import shutdown as shutdown_routes
from .panel import manual_path, status_payload
from .panel import render as render_panel
from .status import providers_facts

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101

#: POST routes. They share the visitor checks (Origin, Content-Type) and
#: nothing else: `/route` plans a turn, it does not run one.
POST_ROUTES = ("/v1/chat/completions", "/v1/messages", "/route", "/rag/reindex",
               "/tools/execute", "/shutdown")

#: The implementations behind `POST /tools/<name>`. One table, so the catalogue and
#: the dispatch cannot disagree about what exists; a name in `catalog.TOOLS` with no
#: entry here answers 500 "catalogued but not wired", which is the bug it is.
TOOL_CALLS = {
    "desktop_windows": desktop.windows,
    "desktop_snapshot": desktop.snapshot,
    "desktop_find": desktop.find,
    "desktop_click": desktop.click,
    "desktop_type": desktop.type_text,
    "desktop_open_url": desktop.open_url,
}

#: A handled refusal is a status the client can act on, not a 500.
TOOL_REFUSAL_STATUS = {
    "platform_unavailable": 503,
    "backend_missing": 503,
    "backend_failed": 500,
    "stale_ref": 409,
    "target_not_found": 404,
    "action_failed": 502,
    "unsafe_url": 400,
    "invalid_url": 400,
    "invalid_request": 400,
    "unsupported_target": 400,
    "browser_failed": 502,
    "browser_refused": 502,
}


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
    #: The server this handler serves. `make_server` sets it per instance; the
    #: shutdown route needs it to close the socket it is answering on.
    owner: ThreadingHTTPServer | None = None

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
        if path == "/modes":
            # Permissions, not prose: a client must know what a mode may read,
            # write and call before it picks one. `source` is never exposed - it
            # is a filesystem path, and to_dict() already leaves it out.
            modes = [spec.to_dict() for _name, spec in sorted(load_modes().items())]
            self._json(200, {"modes": modes, "count": len(modes)})
            return
        if path == "/tools" or path.startswith("/tools/"):
            self._tools_catalog(path)
            return
        if path in ("/", "/index.html"):
            self._html(200, render_panel(
                version=__version__, mode=self.mode,
                harness=getattr(self.adapter, "name", type(self.adapter).__name__),
                shutdown_token=self._shutdown_token(),
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
        if path == "/rag/status":
            # What is on disk, and whether the separate RAG service is up. No
            # index is ever built here: it is compiled from the operator's notes.
            self._json(200, memory_routes.rag_status())
            return
        if path == "/siyuan/blocks":
            query = parse_qs(urlparse(self.path).query)
            limit = (query.get("limit") or [str(memory_routes.DEFAULT_BLOCKS)])[0]
            wanted = (query.get("path") or [""])[0]
            try:
                payload = memory_routes.siyuan_blocks(limit=int(limit), path=wanted)
            except ValueError:
                self._json(400, {"error": {"message": "limit must be an integer",
                                           "type": "invalid_request"}})
                return
            except memory_routes.MemoryRouteError as exc:
                self._json(exc.status, {"error": {"message": str(exc), "type": "memory"}})
                return
            self._json(200, payload)
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
        # `/tools/<name>` is the generic call surface for the catalogue: one route
        # per tool would make the API a second registry. `/tools/execute` keeps its
        # own handler because it predates the catalogue and carries a body shape
        # (code, timeout, mode) that is not a parameter object.
        generic_tool = (
            path.startswith("/tools/")
            and path.count("/") == 2
            and path != "/tools/execute"
        )
        if path not in POST_ROUTES and not generic_tool:
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
        if path == "/route":
            self._plan(payload)
        elif path == "/rag/reindex":
            self._rag_reindex(payload)
        elif path == "/tools/execute":
            self._tools_execute(payload)
        elif path.startswith("/tools/"):
            self._tools_call(path.split("/", 2)[2], payload)
        elif path == "/shutdown":
            self._shutdown(payload)
        elif path == "/v1/chat/completions":
            self._openai(payload)
        else:
            self._anthropic(payload)

    def _rag_reindex(self, payload: dict[str, Any]) -> None:
        """Serve ``POST /rag/reindex``: ask the RAG service to reload its index.

        The name comes from the client's intent; the body says what happened.
        There is no index builder in this repository, so a reindex from scratch
        would have nothing to run - see ``uhb/services/rag/README.md``.
        """
        service = str(payload.get("service_url") or memory_routes.rag_service_url())
        try:
            result = memory_routes.rag_reload(service_url=service)
        except memory_routes.MemoryRouteError as exc:
            self._json(exc.status, {"error": {"message": str(exc), "type": "memory"}})
            return
        self._json(200, result)

    # -- tools ----------------------------------------------------------------

    def _shutdown_token(self) -> str:
        """The operator token, or nothing: no token means no button on the panel."""
        try:
            return shutdown_routes.resolve_token(create=True)
        except shutdown_routes.ShutdownError:
            return ""

    def _shutdown(self, payload: dict[str, Any]) -> None:
        """Serve ``POST /shutdown``: stop the stack, answer before the server dies.

        Three gates before anything moves. A **token** (``X-UHB-Token``, or
        ``token`` in the body) so a stray page or script cannot kill the stack;
        **``{"confirm": true}``** — exactly true, not truthy — so a fat finger in
        a UI cannot either; and the **target list**, checked against a closed set
        instead of silently ignoring a typo.

        ``dry_run`` stops after describing the plan. Otherwise the response is
        written and flushed first, and a background thread stops Docker and the
        local processes, then the socket this request arrived on — which is why
        the interface reads "stopping" and never a connection reset.
        """
        given = self.headers.get(shutdown_routes.TOKEN_HEADER) or payload.get("token")
        if not shutdown_routes.token_matches(given):
            self._json(401, {"error": {
                "message": "a shutdown token is required: send it in the "
                           f"{shutdown_routes.TOKEN_HEADER} header (the operator's copy "
                           f"is {shutdown_routes.token_path()})",
                "type": "unauthorized",
                "header": shutdown_routes.TOKEN_HEADER,
            }})
            return
        if payload.get("confirm") is not True:
            self._json(403, {"error": {
                "message": 'stopping every service needs {"confirm": true}: an explicit '
                           "confirmation, not a stray click",
                "type": "forbidden"}})
            return
        raw = payload.get("stop")
        if raw is None:
            targets: tuple[str, ...] = shutdown_routes.DEFAULT_TARGETS
        elif isinstance(raw, str) and raw.strip():
            targets = (raw.strip(),)
        elif isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
            targets = tuple(item.strip() for item in raw)
        else:
            self._json(400, {"error": {
                "message": f"stop must be one of {', '.join(shutdown_routes.TARGETS)}, "
                           "or a list of them",
                "type": "invalid_request_error"}})
            return
        unknown = [name for name in targets if name not in shutdown_routes.TARGETS]
        if unknown:
            self._json(400, {"error": {
                "message": f"unknown target(s): {', '.join(unknown)}; expected "
                           f"{', '.join(shutdown_routes.TARGETS)}",
                "type": "invalid_request_error"}})
            return
        try:
            state = shutdown_routes.plan(targets=targets)
        except shutdown_routes.ShutdownError as exc:
            self._json(500, {"error": {"message": str(exc), "type": "shutdown"}})
            return
        if payload.get("dry_run"):
            self._json(200, {"dry_run": True, **state.to_dict()})
            return
        self._json(202, {"accepted": True, "plan": state.to_dict(), "self_stops_in_s": 0.5})
        self._stop_in_background(targets)

    def _stop_in_background(self, targets: tuple[str, ...]) -> None:
        """Stop the world from a thread, after the 202 has left the socket."""
        owner = self.owner

        def run() -> None:
            time.sleep(0.5)  # the client must receive its answer first
            try:
                report = shutdown_routes.shutdown(targets=targets)
            except Exception as exc:  # noqa: BLE001 - a failed stop is reported, never raised
                print(f"[uhb] shutdown failed: {type(exc).__name__}: {exc}", flush=True)
                return
            for step in report["steps"]:
                print(
                    f"[uhb] shutdown {step['target']}: {step['detail']}"
                    + ("" if step["ok"] else " (FAILED)"),
                    flush=True,
                )
            if "self" in targets and owner is not None:
                owner.shutdown()
                owner.server_close()

        threading.Thread(target=run, name="uhb-shutdown", daemon=True).start()

    # -- catalogue ------------------------------------------------------------

    def _tools_catalog(self, path: str) -> None:
        """Serve the tool catalogue: read-only, and it can never grant anything.

        ``GET /tools`` lists every tool, ``GET /tools/<name>`` details one. Both
        accept ``?mode=`` — the *default* is the mode this server serves, and an
        explicit value answers a what-if question without changing any permission:
        executing still goes through ``POST /tools/execute``, which re-checks the
        mode it was started with.
        """
        request_id = uuid4().hex[:12]
        query = parse_qs(urlparse(self.path).query)
        requested = (query.get("mode") or [""])[0].strip().lower()
        mode = requested or self.mode
        if mode:
            try:
                get_mode(mode)
            except UnknownMode as exc:
                self._json(404, {
                    "error": {"message": str(exc), "type": "unknown_mode",
                              "hint": "GET /modes lists the modes this build ships"},
                    "request_id": request_id,
                })
                return

        name = path.split("/", 2)[2] if path.startswith("/tools/") else ""
        try:
            payload = catalog.describe(name=name or None, mode=mode or None)
        except catalog.UnknownTool as exc:
            self._json(404, {
                "error": {"message": str(exc), "type": "not_found",
                          "hint": "GET /tools lists every name"},
                "request_id": request_id,
            })
            return

        payload["request_id"] = request_id
        self._json(200, payload)

    def _tools_call(self, name: str, payload: dict[str, Any]) -> None:
        """Call one catalogued tool by name: the mode first, then the tool.

        Same permission check as ``/tools/execute``, and it happens before the
        implementation is looked up: a tool the mode does not allow is refused even
        if it does not exist, so the answer cannot be used to enumerate.
        """
        request_id = uuid4().hex[:12]
        spec = catalog.get(name)
        if spec is None:
            known = ", ".join(sorted(spec.name for spec in catalog.specs()))
            self._json(404, {
                "error": {"message": f"unknown tool {name!r}; known: {known}",
                          "type": "not_found",
                          "hint": "GET /tools lists every name"},
                "request_id": request_id,
            })
            return
        try:
            get_mode(self.mode).check_tool(name)
        except (ModeViolation, ModeRequired, UnknownMode) as exc:
            self._refuse_tool(403, str(exc), {
                "error": {"message": str(exc), "type": "forbidden"},
                "request_id": request_id,
            })
            return
        function = TOOL_CALLS.get(name)
        if function is None:
            self._json(500, {
                "error": {"message": f"tool {name!r} is catalogued but not wired",
                          "type": "configuration",
                          "hint": "a catalogue entry without an implementation is a bug"},
                "request_id": request_id,
            })
            return
        kwargs = {key: value for key, value in payload.items()
                  if key in spec.parameters.get("properties", {})}
        try:
            result = function(**kwargs)
        except TypeError as exc:
            self._json(400, {
                "error": {"message": f"bad arguments: {exc}", "type": "invalid_request_error"},
                "request_id": request_id,
            })
            return
        except Exception as exc:  # noqa: BLE001 - an unwired tool never takes the server down
            self._json(500, {
                "error": {"message": f"{type(exc).__name__}: {exc}", "type": "tool_error"},
                "request_id": request_id,
            })
            return
        result["request_id"] = request_id
        if result.get("ok"):
            self._json(200, result)
            return
        code = str((result.get("reason") or {}).get("code") or "")
        self._json(TOOL_REFUSAL_STATUS.get(code, 400), result)

    # -- tools ----------------------------------------------------------------

    def _refuse_tool(self, status: int, reason: str, payload: dict[str, Any]) -> None:
        """Answer a refusal, and journal it: a 403 nobody logged is a bug nobody sees.

        Only the reason and the server's mode are written - never the code, never a
        secret. The reason is the message the client already receives, so the log
        and the answer cannot drift apart.
        """
        print(f"[uhb] tools/execute refused ({status}): {reason} [mode={self.mode}]", flush=True)
        self._json(status, payload)

    def _tools_execute(self, payload: dict[str, Any]) -> None:
        """Serve ``POST /tools/execute``: run a snippet as the server's own mode.

        Two refusals matter here. The mode is the one the server was started
        with: a body naming another mode gets 403, because otherwise
        ``uhb run --mode chat`` would hand execution to any client that asked for
        ``"mode": "dev"`` and the envelope would mean nothing. And a mode whose
        ``tools`` list has no ``execute_code`` (chat, review, observe) gets 403
        from the tool itself, before a byte is written.
        """
        code = payload.get("code")
        if not isinstance(code, str) or not code.strip():
            self._json(400, {"error": {"message": "code must be a non-empty string",
                                       "type": "invalid_request_error"}})
            return
        requested = str(payload.get("mode") or "").strip()
        if requested and requested != self.mode:
            reason = (f"this server serves mode {self.mode!r}; restart it with "
                      f"--mode {requested} to execute on its behalf")
            self._refuse_tool(403, reason, {"error": {"message": reason, "type": "forbidden"}})
            return
        try:
            timeout = float(payload.get("timeout") or TOOL_DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            self._json(400, {"error": {"message": "timeout must be a number",
                                       "type": "invalid_request_error"}})
            return
        include_base64 = bool(payload.get("include_base64") or payload.get("base64"))
        try:
            result = execute_code(
                code,
                mode=self.mode or None,
                timeout=timeout,
                include_base64=include_base64,
            )
        except (ModeRequired, ModeViolation, UnknownMode) as exc:
            self._refuse_tool(403, str(exc), {"error": {"message": str(exc), "type": "forbidden"}})
            return
        except CodeExecutionError as exc:
            self._refuse_tool(
                400, str(exc), {"error": {"message": str(exc), "type": "invalid_request_error"}}
            )
            return
        self._json(200, result.to_dict())

    # -- planning -------------------------------------------------------------

    def _plan(self, payload: dict[str, Any]) -> None:
        """Serve ``POST /route``: the plan, without running the turn.

        No adapter, no network, no token: ``route()`` classifies the prompt and
        reads the local catalog. The body is ``{"prompt": "...", "mode": "...",
        "complexity": "..."}``; the mode falls back to the envelope this instance
        serves (a server started with ``--mode chat`` plans for chat), and a
        forced complexity skips classification. The response is
        ``RouteDecision.to_dict()`` - complexity, score, signals, the ordered
        chain and the reason - so a caller can show what would be spent before
        spending it.
        """
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            self._json(400, {"error": {"message": "prompt must be a non-empty string",
                                       "type": "invalid_request_error"}})
            return
        forced = str(payload.get("complexity") or "").strip()
        if forced and forced not in COMPLEXITIES:
            self._json(400, {"error": {
                "message": f"unknown complexity {forced!r}; expected one of "
                           f"{', '.join(COMPLEXITIES)}",
                "type": "invalid_request_error"}})
            return
        mode_name = str(payload.get("mode") or self.mode or "").strip()
        try:
            decision = route(prompt, mode_name, complexity=forced or None)
        except UnknownMode as exc:  # the caller named a mode that does not exist
            self._json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return
        except UnroutableRequest as exc:  # well-formed, but no model may serve it
            self._json(409, {"error": {"message": str(exc), "type": "unroutable"}})
            return
        except (InvalidCatalog, ModesError) as exc:  # the installation, not the caller
            self._json(500, {"error": {"message": f"mode catalog is not usable: {exc}",
                                       "type": "configuration_error"}})
            return
        self._json(200, decision.to_dict())

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
        """Preflight for the embedded widget, still local-only.

        ``POST`` and the shutdown token header are allowed because an interface on
        another loopback port (the common case) has to preflight before it can ask
        ``/shutdown``: without them the browser blocks the request and the UI would
        report a network failure for a route that is fine. What authorises the stop
        is the token, not this answer — a foreign origin gets no CORS header at all.
        """
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "accept, content-type, x-uhb-token")
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
    # One handler subclass per server: `owner` must point at *this* instance, and a
    # shared class attribute would make every server in a process point at the last
    # one created — the test suite builds several.
    handler = type("BoundHandler", (Handler,), {"owner": None})
    server = ThreadingHTTPServer((host, port), handler)
    handler.owner = server
    return server


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
