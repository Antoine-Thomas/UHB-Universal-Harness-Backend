"""Execution modes — what an agent is allowed to do, declared as data.

A mode is not a prompt fragment. It is the whole envelope of a turn: which files
may be read, which may be written, which tools exist, what the system prompt may
claim, and which model tier the request deserves. Because it is data, adding a
mode means adding one JSON file to ``profiles/`` — no code, no registry edit.

    from uhb.hub.modes import get_mode

    dev = get_mode("dev")
    dev.can_write("uhb/hub/wiki/scripts/ingest.py")   # True
    dev.check_write(".env")                            # raises ModeViolation
    dev.system_prompt                                  # preamble + constraints

Fail-closed by construction:

- an unknown key in a profile is an error, not a silent no-op (a typo must not
  quietly widen a permission)
- ``deny`` wins over ``read`` and ``write``
- nothing is writable unless a glob says so, and no tool is usable unless listed
- a paid model tier is refused unless the profile says ``allow_paid: true``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable

#: Directory of the shipped profiles. One file per mode, the file name is the mode.
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

#: Extra directory overlaid on the shipped one (same name replaces, new name adds).
PROFILES_ENV = "UHB_MODES_DIR"

#: Closed sets. A value outside them is a configuration error.
KINDS = ("execution", "maintenance")
TIERS = ("eco", "free", "paid")

#: Every key a profile may carry. Anything else is refused.
PROFILE_KEYS = (
    "name",
    "description",
    "kind",
    "read",
    "write",
    "deny",
    "tools",
    "system_prompt",
    "constraints",
    "model",
    "allow_paid",
    "max_turns",
)

MODEL_KEYS = ("tier", "temperature", "max_tokens")


class ModesError(RuntimeError):
    """Base class for mode failures."""


class UnknownMode(ModesError, KeyError):
    """No profile carries that name."""


class InvalidModeConfig(ModesError, ValueError):
    """A profile is malformed. The message names the file and the key."""


class ModeViolation(ModesError, PermissionError):
    """An operation the mode does not allow. Raised before anything is touched."""


class ModeRequired(ModesError, ValueError):
    """A write path was called without an envelope. Fail closed, name the call site."""


# --- path handling ---------------------------------------------------------


def _normalize(path: str) -> str:
    """Posix form, with ``..`` collapsed — and refused when it escapes the root."""
    text = str(path).replace("\\", "/").strip()
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ModeViolation(f"path escapes the workspace: {path!r}")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _match(path: str, patterns: Iterable[str]) -> bool:
    """Glob match with ``dir/**`` meaning "the directory and everything below"."""
    for pattern in patterns:
        if fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/**") and fnmatchcase(path, pattern[:-3]):
            return True
    return False


def _relative(path: str, workspace: str | Path | None) -> str:
    """The workspace-relative form of ``path``, or a violation."""
    raw = str(path).replace("\\", "/")
    looks_absolute = raw.startswith("/") or (len(raw) > 1 and raw[1] == ":")
    if not looks_absolute:
        return _normalize(raw)
    if workspace is None:
        raise ModeViolation(f"absolute path {path!r} needs a workspace to be judged")
    try:
        return Path(path).resolve().relative_to(Path(workspace).resolve()).as_posix()
    except ValueError as exc:
        raise ModeViolation(f"path outside the workspace: {path!r}") from exc


# --- the mode --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModeSpec:
    """One mode, immutable. Built by :func:`load_modes`, never by hand."""

    name: str
    description: str
    kind: str
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    preamble: str = ""
    constraints: tuple[str, ...] = ()
    tier: str = "eco"
    temperature: float = 0.0
    max_tokens: int = 2048
    allow_paid: bool = False
    max_turns: int = 1
    source: str = field(default="", compare=False)

    # -- permissions --------------------------------------------------------

    def _denied(self, relative: str) -> bool:
        return _match(relative, self.deny)

    def allows_read(self, path: str, workspace: str | Path | None = None) -> bool:
        relative = _relative(path, workspace)
        return not self._denied(relative) and _match(relative, self.read)

    def allows_write(self, path: str, workspace: str | Path | None = None) -> bool:
        relative = _relative(path, workspace)
        return not self._denied(relative) and _match(relative, self.write)

    def allows_tool(self, tool: str) -> bool:
        return tool.strip() in self.tools

    def check_read(self, path: str, workspace: str | Path | None = None) -> str:
        """Return the relative path, or raise :class:`ModeViolation`."""
        relative = _relative(path, workspace)
        if self._denied(relative):
            raise ModeViolation(f"mode {self.name!r} denies {relative!r}")
        if not _match(relative, self.read):
            raise ModeViolation(f"mode {self.name!r} may not read {relative!r}")
        return relative

    def check_write(self, path: str, workspace: str | Path | None = None) -> str:
        """Return the relative path, or raise :class:`ModeViolation` *before* any write."""
        relative = _relative(path, workspace)
        if self._denied(relative):
            raise ModeViolation(f"mode {self.name!r} denies {relative!r}")
        if not self.write:
            raise ModeViolation(f"mode {self.name!r} is read-only")
        if not _match(relative, self.write):
            raise ModeViolation(f"mode {self.name!r} may not write {relative!r}")
        return relative

    def check_tool(self, tool: str) -> str:
        if not self.allows_tool(tool):
            raise ModeViolation(f"mode {self.name!r} has no tool {tool!r}")
        return tool

    # -- prompt -------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """Preamble followed by the numbered constraints, deterministic."""
        lines = [self.preamble.strip()] if self.preamble.strip() else []
        if self.constraints:
            lines.append("")
            lines.append("Constraints:")
            lines.extend(f"- {rule}" for rule in self.constraints)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "read": list(self.read),
            "write": list(self.write),
            "deny": list(self.deny),
            "tools": list(self.tools),
            "system_prompt": self.system_prompt,
            "model": {"tier": self.tier, "temperature": self.temperature, "max_tokens": self.max_tokens},
            "allow_paid": self.allow_paid,
            "max_turns": self.max_turns,
        }


# --- validation ------------------------------------------------------------


def _fail(source: str, message: str) -> InvalidModeConfig:
    return InvalidModeConfig(f"{source}: {message}")


def _strings(source: str, key: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise _fail(source, f"{key} must be a list of non-empty strings")
    for item in value:
        if item.startswith("/") or (len(item) > 1 and item[1] == ":"):
            raise _fail(source, f"{key} must be workspace-relative, got {item!r}")
        if ".." in item.split("/"):
            raise _fail(source, f"{key} must not climb out of the workspace, got {item!r}")
    return tuple(value)


def parse_mode(data: dict[str, Any], source: str = "<memory>") -> ModeSpec:
    """Validate one profile mapping. Unknown keys and bad types are refused."""
    if not isinstance(data, dict):
        raise _fail(source, "a profile must be a JSON object")
    unknown = sorted(set(data) - set(PROFILE_KEYS))
    if unknown:
        raise _fail(source, "unknown key(s): " + ", ".join(unknown))

    name = str(data.get("name") or "").strip()
    if not name:
        raise _fail(source, "name is required")
    kind = str(data.get("kind") or "execution")
    if kind not in KINDS:
        raise _fail(source, f"kind must be one of {', '.join(KINDS)}, got {kind!r}")

    model = data.get("model") or {}
    if not isinstance(model, dict):
        raise _fail(source, "model must be an object")
    unknown_model = sorted(set(model) - set(MODEL_KEYS))
    if unknown_model:
        raise _fail(source, "unknown model key(s): " + ", ".join(unknown_model))
    tier = str(model.get("tier") or "eco")
    if tier not in TIERS:
        raise _fail(source, f"model.tier must be one of {', '.join(TIERS)}, got {tier!r}")
    try:
        temperature = float(model.get("temperature", 0.0))
        max_tokens = int(model.get("max_tokens", 2048))
        max_turns = int(data.get("max_turns", 1))
    except (TypeError, ValueError) as exc:
        raise _fail(source, f"numeric field is not a number: {exc}") from exc
    if max_tokens <= 0 or max_turns <= 0:
        raise _fail(source, "max_tokens and max_turns must be positive")

    allow_paid = data.get("allow_paid", False)
    if not isinstance(allow_paid, bool):
        raise _fail(source, "allow_paid must be true or false")

    return ModeSpec(
        name=name,
        description=str(data.get("description") or "").strip(),
        kind=kind,
        read=_strings(source, "read", data.get("read")),
        write=_strings(source, "write", data.get("write")),
        deny=_strings(source, "deny", data.get("deny")),
        tools=_strings(source, "tools", data.get("tools")),
        preamble=str(data.get("system_prompt") or "").strip(),
        constraints=_strings(source, "constraints", data.get("constraints")),
        tier=tier,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_paid=allow_paid,
        max_turns=max_turns,
        source=source,
    )


# --- registry --------------------------------------------------------------

_CACHE: dict[str, dict[str, ModeSpec]] = {}


def _profile_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def load_modes(directory: str | Path | None = None, *, refresh: bool = False) -> dict[str, ModeSpec]:
    """Load every profile of ``directory`` overlaid on the shipped ones.

    The registry is read once and memoised per directory: a running process does
    not silently *gain* permissions because a file changed under it. Reloading is
    an explicit act — ``load_modes(..., refresh=True)`` or :func:`clear_cache`.
    """
    key = str(Path(directory).resolve()) if directory else str(PROFILES_DIR)
    if not refresh and key in _CACHE:
        return dict(_CACHE[key])

    layers = [PROFILES_DIR]
    if directory:
        layers.append(Path(directory))
    env_dir = os.environ.get(PROFILES_ENV)
    if env_dir and Path(env_dir).is_dir():
        layers.append(Path(env_dir))

    files: list[Path] = []
    for layer in layers:
        files.extend(_profile_files(layer))

    modes: dict[str, ModeSpec] = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidModeConfig(f"{path}: unreadable profile ({exc})") from exc
        spec = parse_mode(data, source=path.name)
        modes[spec.name] = spec
    if not modes:
        raise InvalidModeConfig(f"no profile found in {PROFILES_DIR}")
    _CACHE[key] = modes
    return dict(modes)


def get_mode(name: str, directory: str | Path | None = None) -> ModeSpec:
    """One mode by name. Unknown names list what exists — never a silent default."""
    modes = load_modes(directory)
    try:
        return modes[name.strip().lower()]
    except KeyError:
        raise UnknownMode(
            f"unknown mode {name!r}; known modes: {', '.join(sorted(modes))}"
        ) from None


def names(directory: str | Path | None = None) -> list[str]:
    return sorted(load_modes(directory))


def clear_cache() -> None:
    """Drop the memoised registries (tests, and long-lived processes after a deploy)."""
    _CACHE.clear()
