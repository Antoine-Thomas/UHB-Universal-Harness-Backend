"""``uhb status`` — one honest snapshot of what this installation can do.

The report answers three questions and nothing else: what version is installed,
which providers could actually be called right now, and which models the policy
would pick. A provider's key is reported as **loaded or not** — the value is
never read, never printed, never stored.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..core.router import Router
from ..hub.modes import load_modes
from ..hub.router import load_catalog

#: ``(argv, cwd) -> (returncode, stdout, stderr)``. Injectable so tests never run git.
Runner = Callable[[list[str], Path], tuple[int, str, str]]


def _default_runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", type(exc).__name__
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def repo_root(start: Path | None = None, runner: Runner | None = None) -> Path | None:
    """The git worktree containing ``start``, or None when there is none."""
    run = runner or _default_runner
    base = Path(start or Path(__file__).resolve().parents[2])
    code, out, _err = run(["git", "rev-parse", "--show-toplevel"], base)
    if code != 0 or not out:
        return None
    return Path(out.splitlines()[0])


def git_facts(root: Path | None, runner: Runner | None = None) -> dict[str, Any]:
    """Branch, short commit and dirtiness. Absent git is a fact, not an error."""
    run = runner or _default_runner
    if root is None:
        return {"root": "", "branch": "", "commit": "", "dirty": None}
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    commit = run(["git", "rev-parse", "--short", "HEAD"], root)
    status = run(["git", "status", "--porcelain"], root)
    return {
        "root": str(root),
        "branch": branch[1].splitlines()[0] if branch[0] == 0 and branch[1] else "",
        "commit": commit[1].splitlines()[0] if commit[0] == 0 and commit[1] else "",
        "dirty": (bool(status[1]) if status[0] == 0 else None),
    }


def providers_facts(router: Router) -> list[dict[str, Any]]:
    """Every provider with a boolean saying whether its key variable is set."""
    rows = []
    for name, entry in sorted(router.providers.items()):
        env = str(entry.get("api_key_env", ""))
        rows.append(
            {
                "name": name,
                "protocol": str(entry.get("protocol", "openai")),
                "base_url": str(entry.get("base_url", "")),
                "models": list(entry.get("models") or []),
                # a NAME and a boolean: the value never leaves the environment
                "key_env": env,
                "key_loaded": bool(env) and env in os.environ,
                "local": "127.0.0.1" in str(entry.get("base_url", "")) or "localhost" in str(entry.get("base_url", "")),
            }
        )
    return rows


def snapshot(workspace: str | Path | None = None, *, runner: Runner | None = None) -> dict[str, Any]:
    """The whole picture, as data. ``render()`` is the only thing that formats it."""
    router = Router.from_config()
    catalog = load_catalog(router=router)
    root = repo_root(runner=runner)

    return {
        "version": __version__,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "workspace": str(Path(workspace or Path.cwd()).resolve()),
        "repo": git_facts(root, runner),
        "router": {
            "models": [
                {"ref": candidate.ref, "tier": candidate.tier, "complexity": list(candidate.complexity)}
                for candidate in catalog.candidates
            ],
            "tiers": list(catalog.tiers),
        },
        "providers": providers_facts(router),
        "modes": sorted(load_modes()),
    }


def render(data: dict[str, Any]) -> str:
    """Human-readable status. No credential, no absolute path outside the repo block."""
    lines = [
        f"UHB (Universal Harness Backend) {data['version']}",
        f"python        {data['python']}",
        f"workspace     {data['workspace']}",
    ]
    repo = data["repo"]
    if repo["commit"]:
        state = "dirty" if repo["dirty"] else "clean"
        lines.append(f"repository    {repo['branch']}@{repo['commit']} ({state})")
    else:
        lines.append("repository    (not a git checkout)")

    lines.append("")
    lines.append("providers")
    for row in data["providers"]:
        if row["local"]:
            state = "local, no key needed"
        else:
            state = f"{row['key_env']}: {'loaded' if row['key_loaded'] else 'MISSING'}"
        lines.append(f"  {row['name']:<10} {row['protocol']:<9} {len(row['models'])} model(s)  {state}")

    lines.append("")
    lines.append("routing policy")
    for entry in data["router"]["models"]:
        lines.append(f"  {entry['tier']:<5} {entry['ref']:<34} {'/'.join(entry['complexity'])}")

    lines.append("")
    lines.append("modes         " + ", ".join(data["modes"]))
    return "\n".join(lines)
