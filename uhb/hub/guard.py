"""Write gate — the permission layer, applied before anything reaches the disk.

Every HUB write goes through :func:`commit`: it checks *all* planned paths
against the mode first, then writes. A denial therefore leaves the tree exactly
as it was — no half-compiled wiki, no catalogue pointing at a page that was
never written.

    from uhb.hub.guard import commit
    from uhb.hub.modes import get_mode

    commit(get_mode("review"), [(path, text)])   # raises ModeViolation, writes nothing
    commit(get_mode("dev"), [(path, text)], project=repo_root)

``mode`` is mandatory. There is no "no envelope" path: a write that cannot name
the mode it acts for is a hole, so it raises :class:`ModeRequired` before touching
anything. Callers that genuinely want a looser policy write their own profile —
one JSON file, validated like every other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .modes import ModeRequired, ModeSpec, ModeViolation, get_mode


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One write that has passed the gate: the path, the bytes, the relative form."""

    path: Path
    text: str
    relative: str


def _spec(mode: ModeSpec | str | None) -> ModeSpec:
    """Resolve the envelope, or refuse: a write path without a mode is a hole."""
    if mode is None:
        raise ModeRequired(
            "this write path needs a mode: pass one (get_mode('dev'), get_mode('review'), "
            "or a profile of your own) — an implicit envelope is not a permission"
        )
    return get_mode(mode) if isinstance(mode, str) else mode


def check(
    mode: ModeSpec | str,
    path: str | Path,
    *,
    project: str | Path | None = None,
) -> str:
    """Return the workspace-relative path, or raise :class:`ModeViolation`.

    Pure: nothing is created, nothing is opened. ``project`` is the tree the
    mode's globs are written against — not necessarily the wiki root, since a
    wiki usually lives inside a larger repository.
    """
    return _spec(mode).check_write(path, workspace=project)


def preflight(
    mode: ModeSpec | str,
    items: Sequence[tuple[str | Path, str]],
    *,
    project: str | Path | None = None,
) -> list[PlannedWrite]:
    """Check every planned write. Raises on the first denial, writes nothing."""
    spec = _spec(mode)
    planned: list[PlannedWrite] = []
    for path, text in items:
        planned.append(
            PlannedWrite(
                path=Path(path),
                text=text,
                relative=check(spec, path, project=project),
            )
        )
    return planned


def commit(
    mode: ModeSpec | str,
    items: Iterable[tuple[str | Path, str]],
    *,
    project: str | Path | None = None,
) -> list[str]:
    """Gate the whole batch, then write it. Returns the relative paths written."""
    planned = preflight(mode, list(items), project=project)
    if not planned:
        return []
    # Local import: uhb.hub.wiki.scripts is imported by ingest, which imports
    # this module. Resolved at call time, after every module is loaded.
    from .wiki.scripts import atomic_write

    for item in planned:
        atomic_write(item.path, item.text)
    return [item.relative for item in planned]


def write(
    mode: ModeSpec | str,
    path: str | Path,
    text: str,
    *,
    project: str | Path | None = None,
) -> str:
    """Gate then write a single file. The convenience form of :func:`commit`."""
    return commit(mode, [(path, text)], project=project)[0]
