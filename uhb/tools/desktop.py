"""Desktop control: the OS accessibility tree, exposed as tools.

The model is agent-desktop's, and it is the reason this module is small:

* **snapshot → ref → action.** A ref is only valid for the snapshot that produced
  it. An element tree changes under you — a page loads, a dialog opens — and acting
  on a stale reference manipulates something else. `STALE_REF` is a refusal, never
  a best guess;
* **observation and action are different tools.** `desktop_snapshot`,
  `desktop_windows` and `desktop_find` inform; `desktop_click`, `desktop_type` and
  `desktop_open_url` act. Reading the tree grants nothing;
* **the platform is part of the answer.** No accessibility backend on this machine
  is `platform_unavailable` with a hint, not an exception.

Backends: Windows uses UI Automation through the optional `uiautomation` package
(`pip install "uhb[desktop]"`). macOS and Linux are not wired yet — they return the
same structured refusal instead of pretending. That is honest, and it is the
prerequisite the error contract asks for.

Everything a snapshot returns can contain private content: window titles, field
values, text on screen. It is returned to the caller and never written to a log.
"""

from __future__ import annotations

import sys
import uuid
import webbrowser
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

MAX_NODES = 250
MAX_NAME_CHARS = 200
ALLOWED_SCHEMES = ("http", "https")
#: How many snapshots stay resolvable. Small on purpose: a ref from ten snapshots
#: ago describes a screen that no longer exists.
SNAPSHOT_KEEP = 4


@dataclass(frozen=True)
class Snapshot:
    """The refs of one look at the tree, and the nodes behind them."""

    id: str
    app: str
    nodes: dict[str, Any]
    count: int


_SNAPSHOTS: dict[str, Snapshot] = {}


def _ui() -> Any:
    """The Windows backend, or None. Import is lazy: `uhb` must run without it."""
    if sys.platform != "win32":
        return None
    try:
        import uiautomation
    except Exception:  # noqa: BLE001 - a missing extra is a state, not a crash
        return None
    return uiautomation


def backend_status() -> dict[str, Any]:
    """What this machine can do, stated once, without raising."""
    module = _ui()
    platform = "windows" if sys.platform == "win32" else (
        "macos" if sys.platform == "darwin" else "linux"
    )
    if module is not None:
        return {"platform": platform, "backend": "uiautomation", "available": True,
                "reason": None}
    reasons = {
        "windows": ("backend_missing",
                    'install the optional extra: pip install "uhb[desktop]"'),
        "macos": ("platform_unavailable",
                  ("macOS accessibility is not wired in this build; a Python backend "
                   "(AX) would be needed, plus the Accessibility grant")),
        "linux": ("platform_unavailable",
                  "AT-SPI is not wired in this build; at-spi2-core would be needed"),
    }
    code, hint = reasons[platform]
    return {"platform": platform, "backend": "", "available": False,
            "reason": {"code": code, "hint": hint}}


def _refusal(code: str, hint: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": {"code": code, "hint": hint, **extra}}


def _check_backend() -> dict[str, Any] | None:
    status = backend_status()
    if status["available"]:
        return None
    return {"ok": False, "reason": status["reason"], "platform": status["platform"]}


def _validate_url(url: str) -> tuple[str, dict[str, Any] | None]:
    text = (url or "").strip()
    if not text:
        return "", _refusal("invalid_url", "url is required")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return "", _refusal(
            "unsafe_url",
            f"only {', '.join(ALLOWED_SCHEMES)} is opened from here; "
            f"{parsed.scheme or 'no scheme'} is refused",
            scheme=parsed.scheme,
        )
    if not parsed.netloc:
        return "", _refusal("invalid_url", "the URL has no host")
    return text, None


def open_url(url: str, target: str = "browser") -> dict[str, Any]:
    """Open a URL in the operator's browser. `file:` and `javascript:` are refused.

    No accessibility backend is needed for this one: `webbrowser` is the standard
    library's contract with the desktop, and it works on the three platforms.
    """
    text, error = _validate_url(url)
    if error is not None:
        return error
    if target not in ("browser", ""):
        return _refusal("unsupported_target",
                        f"target {target!r} is not served by this build; use 'browser'")
    try:
        opened = webbrowser.open(text, new=2)
    except Exception as exc:  # noqa: BLE001 - a browser that refuses is a result
        return _refusal("browser_failed", f"{type(exc).__name__}: {exc}")
    return {
        "ok": bool(opened),
        "url": text,
        "target": "browser",
        "post_state": {"opened": bool(opened)},
        **({} if opened else {"reason": {"code": "browser_refused",
                                         "hint": "the system reported no browser to open"}}),
    }


def _walk(root: Any, limit: int) -> Iterable[Any]:
    """Depth-first over the UIA tree, bounded. Never unbounded recursion."""
    seen = 0
    stack = [root]
    while stack and seen < limit:
        node = stack.pop()
        yield node
        seen += 1
        try:
            children = node.GetChildren()
        except Exception:  # noqa: BLE001 - a node can vanish mid-walk
            children = []
        stack.extend(reversed(children))


def _describe(node: Any) -> dict[str, Any] | None:
    try:
        name = (node.Name or "")[:MAX_NAME_CHARS]
        role = node.ControlTypeName or ""
        automation_id = node.AutomationId or ""
        if not (name or automation_id):
            return None
        rect = node.BoundingRectangle
        bounds = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
        return {
            "role": role,
            "name": name,
            "automation_id": automation_id,
            "bounds": bounds,
            "enabled": bool(node.IsEnabled),
            "offscreen": bool(node.IsOffscreen),
        }
    except Exception:  # noqa: BLE001 - a control without a rectangle is not usable
        return None


def _remember(app: str, nodes: dict[str, Any]) -> Snapshot:
    snapshot = Snapshot(id=uuid.uuid4().hex[:8], app=app, nodes=nodes, count=len(nodes))
    _SNAPSHOTS[snapshot.id] = snapshot
    while len(_SNAPSHOTS) > SNAPSHOT_KEEP:
        _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
    return snapshot


def windows() -> dict[str, Any]:
    """The top-level windows on screen: title and process, nothing else."""
    refusal = _check_backend()
    if refusal is not None:
        return refusal
    ui = _ui()
    try:
        root = ui.GetRootControl()
        found = []
        for child in root.GetChildren():
            info = _describe(child)
            if info is not None and not info["offscreen"]:
                found.append({"name": info["name"], "role": info["role"],
                              "bounds": info["bounds"]})
    except Exception as exc:  # noqa: BLE001 - the desktop is not always readable
        return _refusal("backend_failed", f"{type(exc).__name__}: {exc}")
    return {"ok": True, "windows": found, "count": len(found),
            "post_state": {"windows": len(found)}}


def snapshot(app: str = "", max_nodes: int = MAX_NODES) -> dict[str, Any]:
    """Read the accessibility tree, with a ref per element. Observation only."""
    refusal = _check_backend()
    if refusal is not None:
        return refusal
    ui = _ui()
    limit = max(1, min(int(max_nodes or MAX_NODES), MAX_NODES))
    try:
        root = ui.GetRootControl()
        if app.strip():
            wanted = app.strip().lower()
            candidates = [c for c in root.GetChildren()
                          if wanted in ((c.Name or "").lower())]
            if not candidates:
                return _refusal("target_not_found",
                                f"no window matches {app!r}", app=app)
            root = candidates[0]
        nodes: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        for node in _walk(root, limit):
            info = _describe(node)
            if info is None or info["offscreen"]:
                continue
            placeholder = f"pending-{len(nodes)}"
            nodes[placeholder] = node
            entries.append({"ref": placeholder, **info})
    except Exception as exc:  # noqa: BLE001
        return _refusal("backend_failed", f"{type(exc).__name__}: {exc}")

    snapshot_id = uuid.uuid4().hex[:8]
    final: dict[str, Any] = {}
    for entry in entries:
        ref = f"{snapshot_id}:{len(final)}"
        final[ref] = nodes.pop(entry["ref"])
        entry["ref"] = ref
    recorded = Snapshot(id=snapshot_id, app=app, nodes=final, count=len(final))
    _SNAPSHOTS[snapshot_id] = recorded
    while len(_SNAPSHOTS) > SNAPSHOT_KEEP:
        _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
    return {"ok": True, "snapshot_id": snapshot_id, "app": app, "elements": entries,
            "count": len(entries), "post_state": {"elements": len(entries)}}


def find(text: str, app: str = "", max_results: int = 20) -> dict[str, Any]:
    """Elements whose name contains `text`, with refs usable by an action."""
    needle = (text or "").strip().lower()
    if not needle:
        return _refusal("invalid_request", "text is required")
    read = snapshot(app=app)
    if not read.get("ok"):
        return read
    matches = [entry for entry in read["elements"] if needle in entry["name"].lower()]
    return {"ok": True, "snapshot_id": read["snapshot_id"], "query": text,
            "matches": matches[: max(1, int(max_results or 20))],
            "count": len(matches),
            "post_state": {"matches": len(matches)}}


def _resolve(ref: str) -> tuple[Any, dict[str, Any] | None]:
    """The control behind a ref, or a refusal naming why it cannot be used."""
    snapshot_id = (ref or "").split(":", 1)[0]
    recorded = _SNAPSHOTS.get(snapshot_id)
    if recorded is None:
        return None, _refusal(
            "stale_ref",
            "this ref belongs to a snapshot that is no longer kept; take a new one",
            ref=ref,
        )
    node = recorded.nodes.get(ref)
    if node is None:
        return None, _refusal("stale_ref", "no element with this ref in that snapshot", ref=ref)
    return node, None


def click(ref: str) -> dict[str, Any]:
    """Activate one element. Refuses a ref it cannot resolve: never clicks blind."""
    refusal = _check_backend()
    if refusal is not None:
        return refusal
    node, error = _resolve(ref)
    if error is not None:
        return error
    try:
        try:
            node.GetInvokePattern().Invoke()
            how = "invoke"
        except Exception:  # noqa: BLE001 - not every control is invokable
            node.Click(simulateMove=False)
            how = "click"
    except Exception as exc:  # noqa: BLE001
        return _refusal("action_failed", f"{type(exc).__name__}: {exc}", ref=ref)
    return {"ok": True, "ref": ref, "how": how,
            "post_state": {"still_present": _still_there(node)}}


def type_text(text: str, ref: str = "") -> dict[str, Any]:
    """Type into the focused element, or into `ref` after focusing it.

    A ref is required when one is given: typing into whatever happens to have focus
    is how a harness sends a message to the wrong window.
    """
    refusal = _check_backend()
    if refusal is not None:
        return refusal
    value = str(text or "")
    if not value:
        return _refusal("invalid_request", "text is required")
    ui = _ui()
    try:
        if ref:
            node, error = _resolve(ref)
            if error is not None:
                return error
            node.SetFocus()
        ui.SendKeys(value, waitTime=0.02)
    except Exception as exc:  # noqa: BLE001
        return _refusal("action_failed", f"{type(exc).__name__}: {exc}", ref=ref)
    return {"ok": True, "ref": ref, "chars": len(value),
            "post_state": {"typed": len(value)}}


def _still_there(node: Any) -> bool:
    try:
        return bool(node.Exists(0.5))
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_NAME_CHARS",
    "MAX_NODES",
    "Snapshot",
    "backend_status",
    "click",
    "find",
    "open_url",
    "snapshot",
    "type_text",
    "windows",
]
