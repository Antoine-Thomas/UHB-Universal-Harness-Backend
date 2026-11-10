"""A ``claude``-compatible binary for Harness Maxxing.

Harness Maxxing launches ``claude -p --output-format stream-json --verbose``, writes
the prompt on stdin as a JSONL user message, and reads JSONL events from stdout
(measured in ``server/maxxing/sessions/runner.py``). This entrypoint accepts that
contract and answers from any UHB adapter.

It is an independent reimplementation: no code from harness-maxxing is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any, TextIO

from ..adapters.hermes import HermesAdapter
from ..core.executor import Executor
from ..core.types import StreamEvent, TaskContext
from ..hub.modes import UnknownMode, get_mode
from ..hub.router import select_model

EXIT_OK = 0
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uhb-claude", add_help=True)
    parser.add_argument("-p", "--prompt", action="store_true")
    parser.add_argument("--output-format", default="stream-json")
    parser.add_argument("--input-format", default="stream-json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--resume", default="")
    parser.add_argument("--permission-mode", default="bypass")
    parser.add_argument("--mode", default="", help="execution mode; the router picks the model")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--workspace", default="")
    return parser


def emit(out: TextIO, payload: dict[str, Any]) -> None:
    out.write(json.dumps(payload, ensure_ascii=False) + "\n")
    out.flush()


def _fail(out: TextIO, session_id: str, message: str) -> None:
    """One assistant error line, then the terminal result a harness waits for."""
    emit(out, {
        "type": "assistant", "session_id": session_id,
        "message": {"role": "assistant", "content": [{"type": "text", "text": f"[uhb] {message}"}]},
    })
    emit(out, {
        "type": "result", "subtype": "error", "is_error": True, "session_id": session_id,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    })


def read_prompt(source: TextIO) -> str:
    """One JSONL user message, or plain text when --input-format text is used."""
    chunks: list[str] = []
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return line
        if message.get("type") == "control_request":
            continue
        content = (message.get("message") or {}).get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if chunks:
            break
    return "\n".join(chunks).strip()


async def _watch_interrupt(executor: Executor, state: dict[str, str], source: TextIO) -> None:
    """A control_request with subtype interrupt cancels the running task."""
    while True:
        line = await asyncio.to_thread(source.readline)
        if not line:
            return
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        request = message.get("request") or {}
        if message.get("type") == "control_request" and request.get("subtype") == "interrupt":
            await executor.cancel(state.get("task_id", ""))
            emit(sys.stdout, {"type": "control_response", "response": {"subtype": "success", "request_id": message.get("request_id")}})
            return


async def run(
    args: argparse.Namespace,
    adapter=None,
    source: TextIO | None = None,
    out: TextIO | None = None,
) -> int:
    source = source or sys.stdin
    out = out or sys.stdout
    adapter = adapter or HermesAdapter(profile=args.profile)
    executor = Executor(adapter)

    prompt = await asyncio.to_thread(read_prompt, source)
    session_id = args.session_id or args.resume or uuid.uuid4().hex
    workspace = args.workspace or ""
    model_ref = args.model or ""
    mode_name = (args.mode or "").strip()
    if mode_name:
        try:
            spec = get_mode(mode_name)  # unknown mode fails before the turn starts
        except UnknownMode as exc:
            _fail(out, session_id, str(exc))
            return EXIT_ERROR
        model_ref = select_model(prompt, spec, model_ref)

    emit(out, {
        "type": "system", "subtype": "init", "session_id": session_id,
        "model": model_ref, "cwd": workspace, "tools": [], "mode": mode_name,
    })

    state: dict[str, str] = {}
    watcher = asyncio.create_task(_watch_interrupt(executor, state, source))
    exit_code = EXIT_OK
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        async for event in executor.execute_task(prompt, TaskContext(
            workspace=workspace, model_ref=model_ref,
            metadata={"session_id": session_id, "mode": mode_name},
        )):
            if event.metadata.get("event") == "start":
                state["task_id"] = str(event.metadata.get("task_id", ""))
                continue
            if event.type == "token" and event.content:
                emit(out, _assistant_text(event, session_id))
            elif event.type == "tool_call":
                emit(out, _assistant_tool(event, session_id))
            elif event.type == "error":
                exit_code = EXIT_ERROR
                emit(out, {"type": "assistant", "session_id": session_id,
                           "message": {"role": "assistant", "content": [
                               {"type": "text", "text": f"[uhb] {event.content}"}]}})
    finally:
        watcher.cancel()

    emit(out, {
        "type": "result", "subtype": "success" if exit_code == EXIT_OK else "error",
        "is_error": exit_code != EXIT_OK, "session_id": session_id, "usage": usage,
    })
    return exit_code


def _assistant_text(event: StreamEvent, session_id: str) -> dict[str, Any]:
    return {"type": "assistant", "session_id": session_id,
            "message": {"role": "assistant", "content": [{"type": "text", "text": event.content}]}}


def _assistant_tool(event: StreamEvent, session_id: str) -> dict[str, Any]:
    block = {"type": "tool_use", "id": event.metadata.get("id", "tool"),
             "name": event.metadata.get("name", "tool"), "input": event.metadata.get("input", {})}
    return {"type": "assistant", "session_id": session_id,
            "message": {"role": "assistant", "content": [block]}}


def main(argv: list[str] | None = None) -> int:
    args, _unknown = build_parser().parse_known_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
