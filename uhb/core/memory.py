"""``sync_memory`` (SPEC.md §2.2).

The contract negotiates the file name with the harness rather than imposing one:
Harness Maxxing reads ``CLAUDE.md``, OpenClaw ``memory.md``, Hermes ``MEMORY.md``.
Writing the same block twice is a no-op, so a replayed turn does not duplicate it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .types import MemorySyncResult

FILENAMES: dict[str, str] = {
    "claude": "CLAUDE.md",
    "harness-maxxing": "CLAUDE.md",
    "openclaw": "memory.md",
    "hermes": "MEMORY.md",
}
DEFAULT_FILENAME = "MEMORY.md"
LEVELS = ("workspace", "project", "topic")
MAX_BYTES = 256 * 1024


class UnknownLevel(ValueError):
    """Raised for a level the contract does not define."""


class MemorySync:
    """Writes one memory block at one level, for one harness convention.

    ``mode`` is mandatory, exactly like the wiki write paths: a memory block is
    a write, and a write without an envelope is a hole. ``scope`` is the tree the
    mode's patterns are judged against (defaults to the workspace).
    """

    def __init__(
        self,
        workspace: str | Path,
        harness: str = "hermes",
        project: str = "",
        topic: str = "",
        mode: object | None = None,
        scope: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.filename = FILENAMES.get(harness, DEFAULT_FILENAME)
        self.project = project
        self.topic = topic
        self.mode = mode
        self.scope = Path(scope) if scope is not None else self.workspace

    # -- the pivot function ---------------------------------------------------

    def sync_memory(self, level: str, content: str) -> MemorySyncResult:
        if level not in LEVELS:
            raise UnknownLevel(f"level must be one of {LEVELS}, got {level!r}")
        content = content or ""
        target = self._target(level)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""

        if _already_present(existing, content):
            # Idempotent by content: nothing written, and the caller can tell.
            return MemorySyncResult(synced=False, level=level, path=str(target))

        block = _block(content)
        payload = existing + ("" if not existing or existing.endswith("\n") else "\n") + block
        if len(payload.encode("utf-8")) > MAX_BYTES:
            payload = payload[:MAX_BYTES]
            payload += "\n\n<!-- truncated: block exceeded the 256 KiB cap -->\n"

        # Local import: uhb.core must stay importable without uhb.hub, and the
        # gate lives with the policy it enforces.
        from ..hub.guard import commit
        from ..hub.modes import ModeRequired

        if self.mode is None:
            raise ModeRequired(
                "MemorySync needs a mode: pass one (get_mode('dev')) so the memory "
                "write is decided by policy, not by whoever called sync_memory"
            )
        commit(self.mode, [(target, payload)], project=self.scope)
        return MemorySyncResult(synced=True, level=level, path=str(target))

    # -- helpers --------------------------------------------------------------

    def _target(self, level: str) -> Path:
        if level == "workspace":
            return self.workspace / self.filename
        if level == "project":
            base = self.workspace / "projects" / self.project if self.project else self.workspace
            return base / self.filename
        base = self.workspace / "projects" / self.project / "topics" / self.topic
        if not self.project or not self.topic:
            base = self.workspace / "topics" / self.topic
        return base / self.filename


def _block(content: str) -> str:
    """A fenced block with a content hash, so identity survives reformatting."""
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:12]
    return f"<!-- uhb:{digest} -->\n{content.strip()}\n"


def _already_present(existing: str, content: str) -> bool:
    if not content.strip():
        return True
    return _block(content) in existing
