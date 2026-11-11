"""Adapter for Hermes Agent, as a runtime.

Hermes is already an agent loop with tools, memory and skills, so the adapter is
an extraction, not a reimplementation: it shells out to the one-shot CLI
(``hermes -z PROMPT``) and forwards each stdout line as a token event.

Nothing here reads a credential: the subprocess inherits the environment it
already had, and no value is ever logged.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterable, Sequence

from ..core.health import Health
from ..core.memory import MemorySync
from ..core.router import Router
from ..core.skills import Skills
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
from .base import UHBAdapter

DEFAULT_TIMEOUT_S = 1800


class HermesAdapter(UHBAdapter):
    """Runs turns through the installed ``hermes`` binary."""

    name = "hermes"

    def __init__(
        self,
        router: Router | None = None,
        profile: str = "default",
        binary: str | Sequence[str] | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        mode: str = "chat",
    ) -> None:
        self.router = router or Router.from_config()
        self.profile = profile
        # A binary, or an argv prefix: `[python, "-m", "my_harness"]` lets a
        # wrapper or a module stand in for the `hermes` entry point — and makes
        # the adapter testable against a real subprocess on every platform.
        chosen = binary if binary is not None else os.environ.get("UHB_HERMES_BIN") or "hermes"
        self.binary: list[str] = [chosen] if isinstance(chosen, str) else list(chosen)
        self.timeout_s = timeout_s
        # The envelope for writes this adapter performs itself (memory). `chat`
        # cannot write, so a caller that wants memory persisted must say which
        # mode it acts for — fail closed by default.
        self.mode = mode
        self._procs: list[asyncio.subprocess.Process] = []

    # -- execute_task ---------------------------------------------------------

    async def execute_task(
        self, prompt: str, context: TaskContext
    ) -> AsyncIterable[StreamEvent]:
        argv = self._argv(prompt, context)
        # pip installs the entry point as a ``hermes.cmd`` shim on Windows, and
        # create_subprocess_exec does not apply PATHEXT the way a shell does:
        # the bare name raises FileNotFoundError. Resolve it ourselves; when
        # ``which`` finds nothing, keep the old name so the error stays the
        # same "binary not found" event. shell=True stays off either way: the
        # prompt is an argument, never part of a command line.
        resolved = shutil.which(argv[0]) or argv[0]
        try:
            proc = await asyncio.create_subprocess_exec(
                resolved,
                *argv[1:],
                cwd=context.workspace or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            yield StreamEvent(
                type="error",
                content=f"binary not found: {' '.join(self.binary)}",
                metadata={"code": type(exc).__name__},
            )
            return

        self._procs.append(proc)
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                # Windows subprocesses end lines with \r\n: strip both, or every
                # token carries a stray carriage return into the harness output.
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    yield StreamEvent(type="token", content=line)
            await asyncio.wait_for(proc.wait(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            yield StreamEvent(type="error", content="timeout", metadata={"code": "Timeout"})
        finally:
            if proc.returncode is None:
                proc.terminate()
            self._procs = [p for p in self._procs if p is not proc]

        if proc.returncode not in (0, None):
            stderr = ""
            if proc.stderr is not None:
                stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            yield StreamEvent(
                type="error",
                content=stderr[-500:] or f"exit {proc.returncode}",
                metadata={"code": "ExitCode", "exit": proc.returncode},
            )

    def _argv(self, prompt: str, context: TaskContext) -> list[str]:
        argv = [*self.binary, "-p", self.profile, "-z", prompt]
        if context.model_ref:
            provider, _, model_id = context.model_ref.partition("/")
            argv += ["--provider", provider, "-m", model_id or provider]
        if context.workspace:
            argv += ["--in", context.workspace]
        return argv

    # -- memory, skills, routing ---------------------------------------------

    async def sync_memory(self, level: str, content: str) -> MemorySyncResult:
        workspace = self._workspace_default()
        sync = MemorySync(workspace, harness="hermes", mode=self.mode, scope=workspace)
        return sync.sync_memory(level, content)

    async def load_skill(self, skill_id: str) -> SkillDescriptor:
        workspace = self._workspace_default()
        return Skills([workspace / "skills", workspace / ".hermes" / "skills"]).load_skill(skill_id)

    def resolve_provider(self, model_ref: str) -> ProviderDescriptor:
        return self.router.resolve_provider(model_ref)

    async def route_model(self, intent: dict) -> ModelRef:
        return self.router.route_model(intent)

    # -- health and cancel ----------------------------------------------------

    async def health(self) -> HealthStatus:
        return await Health(router=self.router, workspace=self._workspace_default()).health()

    async def cancel(self, task_id: str) -> CancelResult:
        if not self._procs:
            return CancelResult(cancelled=False, task_id=task_id, reason="unknown_task")
        proc = self._procs[-1]
        proc.terminate()
        return CancelResult(cancelled=True, task_id=task_id, reason="terminated")

    # -- helpers --------------------------------------------------------------

    def _workspace_default(self) -> Path:
        return Path(os.environ.get("UHB_WORKSPACE") or Path.cwd())
