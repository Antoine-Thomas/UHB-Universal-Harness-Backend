"""The tool catalogue: one definition per tool, taken from the code that runs it.

Two sources of truth, and they never overlap:

* **what a tool is** - this module. Each definition is built from the constants
  the tool itself uses (``TOOL_NAME``, ``MAX_TIMEOUT``, ``MAX_CODE_CHARS``), so a
  limit cannot be described here and enforced somewhere else;
* **who may call it** - ``uhb/hub/modes/profiles/*.json``. The catalogue never
  answers "may I": it answers "what is it, and why is it not available in this
  mode". ``available: true`` is information, never a permission — every call goes
  through :func:`uhb.tools.execute_code`, which re-checks the mode.

Why ``required_permissions`` is empty for every entry today: UHB has exactly one
permission axis, the mode's ``tools`` list. Inventing a second one here would
create a second policy, and two policies is how a surface ends up refusing in one
place and allowing in another.

Nothing in a description names a path, an environment variable or a rule of the
security envelope: the catalogue is served to a model, and a model repeats what
it is told.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..hub.modes import ModeSpec, UnknownMode, load_modes
from . import desktop
from .execute import DEFAULT_TIMEOUT, MAX_CODE_CHARS, MAX_TIMEOUT, TOOL_NAME

CATALOG_VERSION = "1"


@dataclass(frozen=True)
class ToolSpec:
    """One tool, described once."""

    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    execution_type: str
    version: str = "1"
    enabled: bool = True
    #: Extra permissions beyond "the mode lists the tool". Empty today, on purpose.
    required_permissions: tuple[str, ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
            "execution_type": self.execution_type,
            "version": self.version,
            "enabled": self.enabled,
            "required_permissions": list(self.required_permissions),
            "tags": list(self.tags),
        }


EXECUTE_CODE = ToolSpec(
    name=TOOL_NAME,
    description=(
        "Run Python code in the workshop and return its stdout, stderr, exit code "
        "and any image it produced. Use it to compute, to read data or to draw a "
        "chart instead of handing the code to the user. The workshop carries numpy "
        "and matplotlib, and only those: draw with matplotlib and call "
        "pyplot.savefig('name.png') so the image is returned - never turtle, never "
        "tkinter, never a GUI window. The working directory is the run directory, "
        "and it is the only place you may write: a write, a delete or a rename "
        "anywhere else is refused, as are reading the harness's credential files "
        "and starting another process (subprocess, os.system)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
                "maxLength": MAX_CODE_CHARS,
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum execution time in seconds.",
                "minimum": 1,
                "maximum": int(MAX_TIMEOUT),
                "default": int(DEFAULT_TIMEOUT),
            },
            "include_base64": {
                "type": "boolean",
                "description": "Return produced images inline as data URLs instead of paths only.",
                "default": False,
            },
            "expect": {
                "type": "object",
                "description": (
                    "Optional effect to verify after the run: {file, stdout_contains}. "
                    "Absent, no claim of success beyond the exit code is made; a "
                    "declared effect that did not happen answers ok:false with "
                    "reason.code post_state_mismatch."
                ),
                "properties": {
                    "file": {"type": "string",
                             "description": "File name expected inside the run directory."},
                    "stdout_contains": {"type": "string",
                                        "description": "Text expected in stdout."},
                },
                "additionalProperties": False,
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    },
    category="code",
    execution_type="subprocess",
    tags=("python", "artefacts"),
)

#: Every tool UHB knows. A tool that runs must be listed here: the catalogue is
#: what a client reads before it asks for anything.
DESKTOP_WINDOWS = ToolSpec(
    name="desktop_windows",
    description=(
        "List the top-level windows currently on screen, with their title and "
        "bounds. Observation only: it reads the operating system's accessibility "
        "tree and changes nothing."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    category="desktop",
    execution_type="in_process",
    tags=("observation", "windows"),
)

DESKTOP_SNAPSHOT = ToolSpec(
    name="desktop_snapshot",
    description=(
        "Read one window's accessibility tree and return its elements with a "
        "reference per element. References are only valid for this snapshot: if "
        "the screen changed, take a new one instead of acting on a stale ref."
    ),
    parameters={
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Part of the window title to read. Empty reads the desktop root.",
            },
            "max_nodes": {
                "type": "integer",
                "description": "Upper bound on the number of elements returned.",
                "minimum": 1,
                "maximum": desktop.MAX_NODES,
                "default": desktop.MAX_NODES,
            },
        },
        "additionalProperties": False,
    },
    category="desktop",
    execution_type="in_process",
    tags=("observation", "accessibility"),
)

DESKTOP_FIND = ToolSpec(
    name="desktop_find",
    description=(
        "Find elements whose name contains a piece of text, with references that "
        "desktop_click and desktop_type accept. Observation only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Case-insensitive fragment of an element name."},
            "app": {"type": "string", "description": "Part of the window title. Empty reads the desktop root."},
            "max_results": {"type": "integer", "description": "How many matches to return.",
                            "minimum": 1, "maximum": 100, "default": 20},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    category="desktop",
    execution_type="in_process",
    tags=("observation", "search"),
)

DESKTOP_CLICK = ToolSpec(
    name="desktop_click",
    description=(
        "Activate one element by its reference. The reference must come from a "
        "snapshot this server still holds; an unknown or expired one is refused "
        "rather than clicked."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "Reference returned by desktop_snapshot or desktop_find."},
        },
        "required": ["ref"],
        "additionalProperties": False,
    },
    category="desktop",
    execution_type="in_process",
    tags=("action",),
)

DESKTOP_TYPE = ToolSpec(
    name="desktop_type",
    description=(
        "Type text into the focused element, or into a referenced element after "
        "focusing it. A reference is required when one is given: typing into "
        "whatever has focus is how a message lands in the wrong window."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type."},
            "ref": {"type": "string", "description": "Optional element to focus first."},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    category="desktop",
    execution_type="in_process",
    tags=("action", "keyboard"),
)

DESKTOP_OPEN_URL = ToolSpec(
    name="desktop_open_url",
    description=(
        "Open a web address in the operator's default browser. Only http and https "
        "are accepted: file: and javascript: are refused, because a tool that opens "
        "local files is a tool that reads them."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) address to open."},
            "target": {"type": "string", "enum": ["browser"],
                       "description": "Where to open it. Only 'browser' is served by this build.",
                       "default": "browser"},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    category="desktop",
    execution_type="in_process",
    tags=("action", "browser"),
)

TOOLS: tuple[ToolSpec, ...] = (
    EXECUTE_CODE,
    DESKTOP_WINDOWS,
    DESKTOP_SNAPSHOT,
    DESKTOP_FIND,
    DESKTOP_CLICK,
    DESKTOP_TYPE,
    DESKTOP_OPEN_URL,
)

_BY_NAME = {spec.name: spec for spec in TOOLS}


def specs() -> tuple[ToolSpec, ...]:
    return TOOLS


def get(name: str) -> ToolSpec | None:
    return _BY_NAME.get((name or "").strip())


def modes_declaring(name: str, modes: dict[str, ModeSpec] | None = None) -> list[str]:
    """The modes whose ``tools`` list includes ``name``, sorted.

    Public information: ``GET /modes`` already publishes each mode's list, so
    naming them here reveals nothing a client could not read directly.
    """
    registry = modes if modes is not None else load_modes()
    return sorted(mode for mode, spec in registry.items() if name in spec.tools)


def _reason(spec: ToolSpec, mode: ModeSpec | None, declared_by: list[str]) -> dict[str, Any] | None:
    """Why this tool is not available, as a code a client can translate."""
    if not spec.enabled:
        return {"code": "disabled", "hint": "this tool is turned off in this build"}
    if mode is None:
        return None
    if spec.name in mode.tools:
        return None
    payload: dict[str, Any] = {
        "code": "not_in_mode",
        "mode": mode.name,
        "hint": "the current mode does not declare this tool; restart UHB with a mode that does",
    }
    if declared_by:
        payload["available_in"] = declared_by
    return payload


def describe(
    name: str | None = None,
    mode: str | ModeSpec | None = None,
    *,
    modes: dict[str, ModeSpec] | None = None,
) -> dict[str, Any]:
    """The catalogue, optionally narrowed to one tool and annotated for ``mode``.

    Returns ``{"tools": [...], "count": n, "mode": <name or "">}``, or
    ``{"tool": {...}, "count": 1, "mode": ...}`` for a single name. An unknown
    name raises :class:`UnknownTool` so the HTTP layer can answer 404 with the
    list of what exists, the way :func:`uhb.hub.modes.get_mode` does for modes.
    """
    registry = modes if modes is not None else load_modes()
    spec_mode: ModeSpec | None = None
    if isinstance(mode, ModeSpec):
        spec_mode = mode
    elif mode:
        spec_mode = registry.get(str(mode).strip().lower())

    if name:
        spec = get(name)
        if spec is None:
            raise UnknownTool(name, sorted(_BY_NAME))
        return {"tool": _entry(spec, spec_mode, registry), "count": 1,
                "mode": spec_mode.name if spec_mode else ""}

    entries = [_entry(spec, spec_mode, registry) for spec in TOOLS]
    return {"tools": entries, "count": len(entries),
            "mode": spec_mode.name if spec_mode else "", "catalog_version": CATALOG_VERSION}


def _entry(spec: ToolSpec, mode: ModeSpec | None, registry: dict[str, ModeSpec]) -> dict[str, Any]:
    declared_by = modes_declaring(spec.name, registry)
    entry = spec.to_dict()
    entry["compatible_modes"] = declared_by
    # No mode asked for: the catalogue answers "what is it", not "may I".
    entry["available"] = spec.enabled if mode is None else spec.enabled and spec.name in mode.tools
    entry["reason"] = _reason(spec, mode, declared_by)
    return entry


class UnknownTool(LookupError):
    """A name that is not in the catalogue. Carries what does exist."""

    def __init__(self, name: str, known: Iterable[str]) -> None:
        self.name = name
        self.known = list(known)
        super().__init__(
            f"unknown tool {name!r}; known: {', '.join(self.known) or 'none'}"
        )


__all__ = [
    "CATALOG_VERSION",
    "EXECUTE_CODE",
    "TOOLS",
    "ToolSpec",
    "UnknownMode",
    "UnknownTool",
    "describe",
    "get",
    "modes_declaring",
    "specs",
]
