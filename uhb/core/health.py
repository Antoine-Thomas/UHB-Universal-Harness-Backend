"""``health`` (SPEC.md §2.7).

Reachability only. Every probe is a ``GET {base_url}/models`` — never a completion,
never a token. The whole check is bounded so a dead provider cannot hold a UI.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .router import Router
from .types import HealthStatus

PROBE_TIMEOUT_S = 1.0
TOTAL_TIMEOUT_S = 2.0


class Health:
    """Answers "is this backend usable right now" without spending anything."""

    def __init__(
        self,
        router: Router | None = None,
        workspace: str | Path | None = None,
        timeout_s: float = PROBE_TIMEOUT_S,
    ) -> None:
        self.router = router or Router()
        self.workspace = Path(workspace) if workspace else None
        self.timeout_s = timeout_s

    async def health(self) -> HealthStatus:
        checks: dict[str, object] = {}
        workspace_ok = self._workspace_ok()
        checks["workspace"] = {"ok": workspace_ok, "path": str(self.workspace) if self.workspace else ""}

        providers = sorted(self.router.providers)
        results = await asyncio.gather(
            *(self._probe(name) for name in providers), return_exceptions=True
        )
        reachable = 0
        for name, result in zip(providers, results):
            if isinstance(result, Exception):
                checks[name] = {"reachable": False, "error": type(result).__name__}
                continue
            checks[name] = result
            reachable += 1 if result.get("reachable") else 0

        if not workspace_ok:
            status = "down"
        elif reachable == len(providers) or reachable > 0:
            status = "ok" if reachable == len(providers) else "degraded"
        else:
            status = "down"

        return HealthStatus(
            status=status,  # type: ignore[arg-type]
            checks=checks,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    # -- helpers --------------------------------------------------------------

    def _workspace_ok(self) -> bool:
        return self.workspace is None or self.workspace.is_dir()

    async def _probe(self, provider: str) -> dict:
        entry = self.router.providers[provider]
        url = str(entry["base_url"]).rstrip("/") + "/models"
        try:
            import httpx  # local import: health is the only module that needs it

            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await asyncio.wait_for(
                    client.get(url), timeout=self.timeout_s
                )
            return {"reachable": response.status_code < 500, "status": response.status_code}
        except Exception as exc:
            return {"reachable": False, "error": type(exc).__name__}


async def health(router: Router | None = None, workspace: str | Path | None = None) -> HealthStatus:
    """Module-level convenience wrapper, bounded by the contract's 2 s budget."""
    checker = Health(router=router, workspace=workspace)
    return await asyncio.wait_for(checker.health(), timeout=TOTAL_TIMEOUT_S)
