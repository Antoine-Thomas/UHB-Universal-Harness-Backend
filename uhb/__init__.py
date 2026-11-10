"""UHB — Universal Harness Backend, reference implementation.

The public surface is the eight pivot functions (``core.contract``) plus the
typed payloads they exchange (``core.types``). Adapters and entrypoints are
imported explicitly by the caller, so that ``import uhb`` never spawns a
process, opens a socket, or reads a configuration file.
"""

from .core.contract import UHBContract
from .core.types import (
    CancelResult,
    HealthStatus,
    MemorySyncResult,
    ModelRef,
    ProviderDescriptor,
    SkillDescriptor,
    StreamEvent,
    TaskContext,
)

__version__ = "0.1.1"

__all__ = [
    "UHBContract",
    "CancelResult",
    "HealthStatus",
    "MemorySyncResult",
    "ModelRef",
    "ProviderDescriptor",
    "SkillDescriptor",
    "StreamEvent",
    "TaskContext",
    "__version__",
]
