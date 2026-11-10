"""The eight pivot functions, as a typing surface (SPEC.md §2).

Implementation note: ``execute_task`` is declared as a regular ``def`` returning
an ``AsyncIterable``, not as ``async def``. Declaring it ``async`` would force
every caller to await twice — once for the call, once for the iteration — and
would make cancellation arrive one generator frame late.
"""

from __future__ import annotations

from typing import AsyncIterable, Protocol, runtime_checkable

from .types import (
    CancelResult,
    HealthStatus,
    MemorySyncResult,
    ModelRef,
    ProviderDescriptor,
    SkillDescriptor,
    StreamEvent,
    TaskContext,
)


FUNCTIONS: tuple[str, ...] = (
    "execute_task",
    "sync_memory",
    "load_skill",
    "route_model",
    "stream",
    "cancel",
    "health",
    "resolve_provider",
)


@runtime_checkable
class UHBContract(Protocol):
    """What a backend must implement to be usable from a control plane."""

    def execute_task(
        self, prompt: str, context: TaskContext
    ) -> AsyncIterable[StreamEvent]:
        """Run one turn; the first event is ``start``-equivalent, the last `done`."""
        ...

    async def sync_memory(self, level: str, content: str) -> MemorySyncResult:
        """Write memory at workspace / project / topic level, idempotently."""
        ...

    async def load_skill(self, skill_id: str) -> SkillDescriptor:
        """Return a skill descriptor; synchronous content, no network."""
        ...

    async def route_model(self, intent: dict) -> ModelRef:
        """Pick a model from an intent, deterministically."""
        ...

    def stream(self, output: StreamEvent) -> None:
        """Push one event to the consumer. Never blocks, never reorders."""
        ...

    async def cancel(self, task_id: str) -> CancelResult:
        """Ask a task to stop, cooperatively and idempotently."""
        ...

    async def health(self) -> HealthStatus:
        """Report status without spending tokens."""
        ...

    def resolve_provider(self, model_ref: str) -> ProviderDescriptor:
        """Turn ``provider/model`` into protocol, base URL, env name, model id."""
        ...
