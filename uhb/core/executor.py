"""``execute_task``, ``stream`` and ``cancel`` (SPEC.md §2.1, §2.5, §2.6).

The executor owns the task registry and nothing else: it does not know which
adapter runs the turn, it does not pick a model, and it holds no product state.
Cancellation is cooperative — a flag the event loop checks between events — so the
adapter can finish the tool it is running instead of being killed mid-write.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterable, Awaitable, Callable

from .types import CancelResult, StreamEvent, TaskContext

Listener = Callable[[StreamEvent], None]


class Executor:
    """Runs turns through an adapter, and exposes the task lifecycle."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._cancels: dict[str, asyncio.Event] = {}
        self._idempotency: dict[str, str] = {}
        self._listeners: list[Listener] = []

    # -- stream ---------------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def stream(self, output: StreamEvent) -> None:
        """Push one event to every consumer. Never raises into the turn."""
        for listener in list(self._listeners):
            try:
                listener(output)
            except Exception:  # a broken consumer must not kill the task
                continue

    # -- execute_task ---------------------------------------------------------

    def execute_task(self, prompt: str, context: TaskContext) -> AsyncIterable[StreamEvent]:
        """Return the turn as an async generator (see contract.py for the shape)."""
        return self._run(prompt, context)

    async def _run(self, prompt: str, context: TaskContext) -> AsyncIterable[StreamEvent]:
        key = str(context.metadata.get("idempotency_key") or "")
        if key and key in self._idempotency:
            yield StreamEvent(
                type="error",
                content="duplicate",
                metadata={"task_id": self._idempotency[key], "idempotency_key": key},
            )
            return

        task_id = uuid.uuid4().hex[:12]
        self._cancels[task_id] = asyncio.Event()
        if key:
            self._idempotency[key] = task_id

        yield StreamEvent(
            type="token",
            content="",
            metadata={"event": "start", "task_id": task_id, "model_ref": context.model_ref},
        )
        try:
            async for event in self.adapter.execute_task(prompt, context):
                if self._cancels.get(task_id, asyncio.Event()).is_set():
                    break
                if event.type == "done":
                    # The adapter's end-of-stream is a hint, not the terminal
                    # event: the executor owns the single `done` (invariant 3).
                    break
                self.stream(event)
                yield event
        except asyncio.CancelledError:
            yield StreamEvent(type="done", content="", metadata={"task_id": task_id, "reason": "cancelled"})
            raise
        except Exception as exc:  # surface, never swallow (invariant 7)
            error = StreamEvent(
                type="error",
                content=str(exc),
                metadata={"task_id": task_id, "code": type(exc).__name__},
            )
            self.stream(error)
            yield error
            yield StreamEvent(type="done", content="", metadata={"task_id": task_id, "reason": "failed"})
            return
        finally:
            cancelled = self._cancels.get(task_id)
            self._cancels.pop(task_id, None)

        reason = "cancelled" if (cancelled is not None and cancelled.is_set()) else "done"
        final = StreamEvent(type="done", content="", metadata={"task_id": task_id, "reason": reason})
        self.stream(final)
        yield final

    # -- cancel ---------------------------------------------------------------

    async def cancel(self, task_id: str) -> CancelResult:
        event = self._cancels.get(task_id)
        if event is None:
            return CancelResult(cancelled=False, task_id=task_id, reason="unknown_task")
        if event.is_set():
            return CancelResult(cancelled=False, task_id=task_id, reason="already_cancelled")
        event.set()
        return CancelResult(cancelled=True, task_id=task_id, reason="user_stop")

    # -- introspection --------------------------------------------------------

    @property
    def running(self) -> list[str]:
        return [task_id for task_id, flag in self._cancels.items() if not flag.is_set()]
