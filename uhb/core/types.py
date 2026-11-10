"""Typed payloads exchanged across the UHB contract.

These mirror SPEC.md §1. They are plain dataclasses on purpose: a harness on the
other side of the wire receives JSON, and a dataclass is the cheapest thing that
can be mapped to and from it without a schema engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["token", "tool_call", "tool_result", "diff", "done", "error"]
ProtocolName = Literal["openai", "anthropic", "deepseek"]
HealthState = Literal["ok", "degraded", "down"]


@dataclass(slots=True)
class TaskContext:
    """Everything a harness knows about the turn it is asking for."""

    workspace: str
    project: str = ""
    topic: str = ""
    model_ref: str = ""
    mandate: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "project": self.project,
            "topic": self.topic,
            "model_ref": self.model_ref,
            "mandate": dict(self.mandate),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class StreamEvent:
    """One unit of progress. ``type`` is the discriminator, never the content."""

    type: EventType
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ModelRef:
    """A resolved model target: provider namespace plus native model id."""

    provider: str
    model_id: str
    protocol: ProtocolName = "openai"

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model_id}"

    @classmethod
    def parse(cls, raw: str) -> "ModelRef":
        provider, _, model_id = raw.partition("/")
        if not model_id:
            raise ValueError(f"model reference must be 'provider/model', got {raw!r}")
        return cls(provider=provider, model_id=model_id)


@dataclass(slots=True)
class ProviderDescriptor:
    """Call coordinates. ``api_key_env`` is a variable NAME, never a value."""

    protocol: str
    base_url: str
    api_key_env: str
    model_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model_id": self.model_id,
        }


@dataclass(slots=True)
class HealthStatus:
    status: HealthState
    checks: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


@dataclass(slots=True)
class MemorySyncResult:
    synced: bool
    level: str
    path: str


@dataclass(slots=True)
class SkillDescriptor:
    id: str
    name: str
    description: str
    content: str


@dataclass(slots=True)
class CancelResult:
    cancelled: bool
    task_id: str
    reason: str = ""
