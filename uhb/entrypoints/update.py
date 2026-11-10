"""``uhb update`` — fast-forward only, and prove what changed.

There is no ``--force``, no ``reset``, no history rewrite in this module: the
only move it makes is ``git pull --ff-only``, and a tree that cannot fast-forward
is a refusal with the reason, not something to fix by force.

Integrity: the update reports the commit before and after, and refuses to run on
a dirty tree. UHB publishes no separate checksum manifest — the git object graph
is the integrity record, so claiming a checksum check would be theatre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .status import _default_runner, repo_root

Runner = Callable[[list[str], Path], tuple[int, str, str]]


class UpdateError(RuntimeError):
    """The update cannot be honoured safely. Nothing was changed."""


@dataclass(slots=True)
class UpdateResult:
    root: str
    branch: str
    before: str
    after: str
    pulled: bool
    dry_run: bool
    pending: int = 0
    checks: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "branch": self.branch,
            "before": self.before,
            "after": self.after,
            "pulled": self.pulled,
            "dry_run": self.dry_run,
            "pending": self.pending,
            "checks": list(self.checks),
            "detail": self.detail,
        }


def _git(runner: Runner, root: Path, *args: str) -> tuple[int, str, str]:
    return runner(["git", *args], root)


def _require(ok: bool, message: str, detail: str = "") -> None:
    if not ok:
        raise UpdateError(f"{message}{': ' + detail if detail else ''}")


def run_update(
    root: str | Path | None = None,
    *,
    runner: Runner | None = None,
    dry_run: bool = False,
    remote: str = "origin",
) -> UpdateResult:
    """Fetch, fast-forward, verify. Raises on anything that is not a clean advance."""
    run = runner or _default_runner
    base = repo_root(Path(root) if root else None, run)
    _require(base is not None, "not a git checkout: uhb update needs the repository it was installed from")
    assert base is not None

    branch_out = _git(run, base, "rev-parse", "--abbrev-ref", "HEAD")
    _require(branch_out[0] == 0, "cannot read the current branch", branch_out[2])
    branch = branch_out[1].splitlines()[0] if branch_out[1] else "HEAD"

    status = _git(run, base, "status", "--porcelain")
    _require(status[0] == 0, "cannot read the working tree", status[2])
    _require(
        not status[1].strip(),
        "the working tree is dirty: commit or stash first (uhb update never resets anything)",
    )

    before_out = _git(run, base, "rev-parse", "--short", "HEAD")
    _require(before_out[0] == 0, "cannot resolve HEAD", before_out[2])
    before = before_out[1].splitlines()[0]

    fetch = _git(run, base, "fetch", "--prune", remote)
    _require(fetch[0] == 0, f"git fetch {remote} failed", fetch[2])

    upstream = f"{remote}/{branch}"
    ahead = _git(run, base, "rev-list", "--count", f"HEAD..{upstream}")
    pending = int(ahead[1].splitlines()[0]) if ahead[0] == 0 and ahead[1].strip().isdigit() else 0

    if dry_run:
        return UpdateResult(
            root=str(base),
            branch=branch,
            before=before,
            after=before,
            pulled=False,
            dry_run=True,
            pending=pending,
            checks=["working tree clean", f"remote {upstream} reachable"],
        )

    pull = _git(run, base, "pull", "--ff-only", remote, branch)
    _require(
        pull[0] == 0,
        "git pull --ff-only failed: the local branch and the remote have diverged, "
        "and this command will not merge or rebase for you",
        pull[2],
    )

    after_out = _git(run, base, "rev-parse", "--short", "HEAD")
    after = after_out[1].splitlines()[0] if after_out[0] == 0 and after_out[1] else before
    clean = _git(run, base, "status", "--porcelain")

    return UpdateResult(
        root=str(base),
        branch=branch,
        before=before,
        after=after,
        pulled=before != after,
        dry_run=False,
        pending=0,
        checks=[
            "working tree was clean before the pull",
            "fast-forward only (no merge commit, no rewrite)",
            "working tree clean after the pull" if not clean[1].strip() else "WARNING: tree not clean after the pull",
            f"HEAD is {after}",
        ],
        detail="up to date" if before == after else f"{before} -> {after}",
    )
