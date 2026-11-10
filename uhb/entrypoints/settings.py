"""``uhb config`` — read and write the UHB configuration without lying about it.

The file is ``uhb/config.yaml`` (override with ``UHB_CONFIG``); when it is absent
the built-in defaults apply, which is why ``list`` always works on a fresh clone.

Two rules shape this module:

- **A credential value never enters a file.** ``set`` refuses a key whose last
  segment names a secret. The configuration carries the *variable name*
  (``api_key_env``), and the value stays in the environment.
- **A write is checked, then written.** The whole file is parsed, modified and
  replaced atomically; a bad value never leaves a half-written config behind.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CONFIG_ENV = "UHB_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"

#: Any key whose final segment looks like this is refused by ``set``.
FORBIDDEN_KEY = re.compile(r"(api[_-]?key|token|password|secret|credential)", re.IGNORECASE)

#: Top-level blocks a caller may address.
SECTIONS = ("providers", "routing")


class SettingsError(RuntimeError):
    """A configuration operation that cannot be honoured."""


def config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get(CONFIG_ENV)
    return Path(env).expanduser() if env else DEFAULT_CONFIG


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SettingsError(
            "PyYAML is needed to edit config.yaml: pip install pyyaml, "
            "or edit the file by hand (it is documented in config.example.yaml)"
        ) from exc
    return yaml


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Effective configuration: the file if present, else the built-in defaults."""
    from ..core.router import DEFAULT_PROVIDERS, DEFAULT_ROUTING

    target = config_path(path)
    if not target.is_file():
        return {"providers": dict(DEFAULT_PROVIDERS), "routing": dict(DEFAULT_ROUTING)}
    yaml = _yaml()
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SettingsError(f"{target}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{target}: a config file must be a mapping at top level")
    return data


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{providers: {deepseek: {models: [...]}}}`` -> dotted keys, leaves only."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            flat.update(flatten(value, dotted + "."))
        else:
            flat[dotted] = value
    return flat


def get(data: dict[str, Any], key: str) -> Any:
    """One value by dotted path. ``routing`` returns the whole block."""
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            known = ", ".join(sorted(flatten(data))[:8])
            raise SettingsError(f"unknown configuration key {key!r}; examples: {known}")
        node = node[part]
    return node


def _parse_value(raw: str) -> Any:
    """JSON when it parses (numbers, booleans, lists), the raw string otherwise."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def set_value(key: str, raw: str, path: str | Path | None = None) -> tuple[dict[str, Any], Any]:
    """Write one dotted key. Returns ``(new_config, value_written)``."""
    segment = key.split(".")[-1]
    if FORBIDDEN_KEY.search(segment):
        raise SettingsError(
            f"refusing to write {key!r}: a credential value never goes in a config file. "
            "Point api_key_env at an environment variable instead."
        )
    root_key = key.split(".")[0]
    if root_key not in SECTIONS:
        raise SettingsError(
            f"refusing to write {key!r}: only {', '.join(SECTIONS)} are configurable "
            "(a free-form key would be read by nothing)"
        )

    target = config_path(path)
    data = dict(load(target))
    parts = key.split(".")
    node: dict[str, Any] = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    value = _parse_value(raw)
    node[parts[-1]] = value

    yaml = _yaml()
    _atomic_write(target, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return data, value
