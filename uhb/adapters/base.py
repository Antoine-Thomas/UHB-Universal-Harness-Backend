"""``UHBAdapter`` — what every adapter must implement.

An adapter binds the contract to one product. It may add capability, never
subtract it: a function it cannot support returns an explicit refusal instead of
disappearing, so a caller can tell "not supported" from "bug".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterable

from ..core.types import (
    CancelResult,
    HealthStatus,
    MemorySyncResult,
    ModelRef,
    ProviderDescriptor,
    SkillDescriptor,
    StreamEvent,
    TaskContext,
)


class UnsupportedFunction(NotImplementedError):
    """Raised when an adapter cannot honour a contract function."""


class UHBAdapter(ABC):
    """Base class: the eight functions, five of them with a safe default."""

    name: str = "adapter"

    # -- required ------------------------------------------------------------

    @abstractmethod
    def execute_task(
        self, prompt: str, context: TaskContext
    ) -> AsyncIterable[StreamEvent]:
        ...

    @abstractmethod
    async def sync_memory(self, level: str, content: str) -> MemorySyncResult:
        ...

    @abstractmethod
    async def load_skill(self, skill_id: str) -> SkillDescriptor:
        ...

    @abstractmethod
    def resolve_provider(self, model_ref: str) -> ProviderDescriptor:
        ...

    # -- optional, with a default that refuses rather than lies ---------------

    async def route_model(self, intent: dict) -> ModelRef:
        raise UnsupportedFunction(f"{self.name}: route_model is not supported")

    def stream(self, output: StreamEvent) -> None:
        """Adapters that own an event transport override this."""
        return None

    async def cancel(self, task_id: str) -> CancelResult:
        return CancelResult(cancelled=False, task_id=task_id, reason="unknown_task")

    async def health(self) -> HealthStatus:
        raise UnsupportedFunction(f"{self.name}: health is not supported")

    # -- introspection --------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Which pivot functions are really implemented, for `uhb health`."""
        from ..core.contract import FUNCTIONS

        return {fn: _overrides(self, fn) for fn in FUNCTIONS}


def _overrides(adapter: UHBAdapter, fn: str) -> bool:
    from .base import UHBAdapter as Base

    return getattr(type(adapter), fn, None) is not getattr(Base, fn, None)
