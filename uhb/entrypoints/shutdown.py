"""Graceful shutdown: what to stop, in which order, and how to prove nothing is left.

The problem this solves is the one that bites after every manual test: a container
or a keep-alive process still holds 8101, 8200 or 6806, and the next start fails
with "address already in use". One button, one route, and a report that names what
is left.

Three families, each optional, each verified after the fact:

* **docker** — this compose project only (``docker compose down``, never
  ``--volumes``, never ``--rmi``), and only when it actually has containers;
* **local** — processes LISTENING on the stack's ports that are *recognisable* as
  ours (``python``, ``uvicorn``, ``serveur_rag``, ``SiYuan-Kernel``). A polite
  termination request, then a forced kill after :data:`GRACE_SECONDS`. Anything
  else on those ports is reported and left alone: this is a cleanup tool, not a
  weapon against whatever else the operator runs;
* **self** — the UHB server that answered, stopped *after* the HTTP response is
  flushed, so the interface receives "stopped" instead of a connection reset.

Every external effect is injectable (``runner``, ``lister``, ``sleeper``), which is
why the tests never touch Docker and never kill a real process.

The token is the fat-finger guard the operator asked for: ``UHB_SHUTDOWN_TOKEN`` if
set, else ``~/.uhb/shutdown-token`` (created on first use, mode 0600). It is a
local secret between the operator and this machine — it stops a stray page or a
script from killing the stack, not a hostile local user, who can read the file.
"""

from __future__ import annotations

import hmac
import os
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Ports this stack publishes. A listener here is a candidate, not a target.
STACK_PORTS: tuple[int, ...] = (8101, 8200, 6806)

#: How long a process gets to leave on its own before it is forced out.
GRACE_SECONDS = 5.0

TOKEN_ENV = "UHB_SHUTDOWN_TOKEN"
TOKEN_FILE_ENV = "UHB_SHUTDOWN_TOKEN_FILE"
TOKEN_HEADER = "X-UHB-Token"

#: What a request may ask for. Anything else is a 400, not a silent no-op.
TARGETS: tuple[str, ...] = ("docker", "local", "self")
DEFAULT_TARGETS: tuple[str, ...] = TARGETS

#: Process names that belong to this stack on its own ports.
OURS = frozenset(
    {
        "python",
        "python.exe",
        "python3",
        "pythonw.exe",
        "uvicorn",
        "siyuan-kernel.exe",
        "siyuan.exe",
        "serveur_rag.py",
    }
)

#: ``(argv, cwd) -> (returncode, stdout, stderr)``. Injectable so tests run no Docker.
Runner = Callable[[list[str], Path | None], tuple[int, str, str]]
#: ``(pid, signal) -> None``. Injectable for the same reason the runner is:
#: a test must not signal a real pid it never owned.
Signaller = Callable[[int, int], None]
#: ``(port) -> [Listener]``. Injectable so tests kill nothing.
Lister = Callable[[int], list["Listener"]]


class ShutdownError(RuntimeError):
    """The shutdown cannot even be planned: unusable token, unusable directory."""


@dataclass(frozen=True, slots=True)
class Listener:
    """One process holding a port."""

    port: int
    pid: int
    name: str

    def to_dict(self, *, ours: bool | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"port": self.port, "pid": self.pid, "name": self.name}
        if ours is not None:
            payload["ours"] = ours
        return payload


@dataclass(frozen=True, slots=True)
class Step:
    """One thing that was done, or deliberately not done."""

    target: str
    detail: str
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "detail": self.detail, "ok": self.ok}


@dataclass(slots=True)
class Plan:
    """What a shutdown would do, computed before doing anything."""

    targets: tuple[str, ...]
    containers: list[str] = field(default_factory=list)
    listeners: list[Listener] = field(default_factory=list)
    refused: list[Listener] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": list(self.targets),
            "docker": {
                "containers": list(self.containers),
                "action": "docker compose down" if self.containers else "nothing to stop",
            },
            "local": [listener.to_dict(ours=True) for listener in self.listeners],
            "refused": [
                {**listener.to_dict(), "reason": "not a UHB process on this port"}
                for listener in self.refused
            ],
            "self": "stopped after the response" if "self" in self.targets else "kept running",
            "notes": list(self.notes),
        }


# --- the token -------------------------------------------------------------


def token_path() -> Path:
    """Where the generated token lives: ``$UHB_SHUTDOWN_TOKEN_FILE`` or ``~/.uhb``."""
    configured = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".uhb" / "shutdown-token"


def resolve_token(*, create: bool = True) -> str:
    """The operator token: environment first, then the file, then a fresh one."""
    from_env = os.environ.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    path = token_path()
    try:
        if path.is_file():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        if not create:
            return ""
        value = secrets.token_urlsafe(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0600 on POSIX; on Windows the ACL of the user's profile does the work.
        path.write_text(value + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError as exc:
        raise ShutdownError(
            f"cannot read or create the shutdown token at {path}: {exc} — "
            f"set {TOKEN_ENV} in the environment instead"
        ) from exc


def token_matches(given: str | None) -> bool:
    """Constant-time comparison against the resolved token. No token, no match."""
    expected = resolve_token(create=False)
    if not expected or not given:
        return False
    return hmac.compare_digest(str(given).strip(), expected)


# --- injection points ------------------------------------------------------


def _decode(raw: bytes | None) -> str:
    """Decode a child's output without ever raising.

    ``tasklist`` and ``netstat`` answer in the console code page on Windows (cp850
    on a French install), not in UTF-8: decoding with the locale codec killed the
    reader thread and left ``stdout`` as ``None``. Measured while stopping a real
    process — the branch crashed instead of stopping it.
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "cp850", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _default_runner(argv: list[str], cwd: Path | None) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", type(exc).__name__
    return completed.returncode, _decode(completed.stdout).strip(), _decode(completed.stderr).strip()


def in_container() -> bool:
    """True when UHB itself runs inside Docker: then only ``self`` may be stopped."""
    return Path("/.dockerenv").exists()


def _psutil_listeners(port: int) -> list[Listener] | None:
    """psutil if the operator has it; None when it is not installed."""
    try:
        import psutil  # optional, never a dependency
    except ImportError:
        return None
    found: list[Listener] = []
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if not connection.laddr or connection.laddr.port != port:
                continue
            if connection.status != psutil.CONN_LISTEN or not connection.pid:
                continue
            try:
                name = psutil.Process(connection.pid).name()
            except (psutil.Error, OSError):
                name = "?"
            found.append(Listener(port=port, pid=connection.pid, name=name))
    except (psutil.Error, OSError):
        return None
    return found


def _windows_listeners(port: int, runner: Runner) -> list[Listener]:
    code, out, _err = runner(["netstat", "-ano", "-p", "TCP"], None)
    if code != 0:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, state, pid = parts[1], parts[3].upper(), parts[4]
        if not local.endswith(f":{port}") or state != "LISTENING":
            continue
        if pid.isdigit() and int(pid) not in pids:
            pids.append(int(pid))
    found: list[Listener] = []
    for pid in pids:
        _code, listing, _err = runner(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], None
        )
        name = "?"
        for line in listing.splitlines():
            cells = [cell.strip('"') for cell in line.split(",")]
            if len(cells) >= 2 and cells[1].strip().isdigit():
                name = cells[0]
                break
        found.append(Listener(port=port, pid=pid, name=name))
    return found


def _posix_listeners(port: int, runner: Runner) -> list[Listener]:
    code, out, _err = runner(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], None)
    if code != 0:
        return []
    found: list[Listener] = []
    for token in out.split():
        if not token.isdigit():
            continue
        pid = int(token)
        _code, comm, _err = runner(["ps", "-o", "comm=", "-p", str(pid)], None)
        found.append(Listener(port=port, pid=pid, name=comm.splitlines()[0] if comm else "?"))
    return found


def listeners(port: int, *, runner: Runner | None = None) -> list[Listener]:
    """Who is LISTENING on ``port``. psutil, then the platform's own tools."""
    via_psutil = _psutil_listeners(port)
    if via_psutil is not None:
        return via_psutil
    run = runner or _default_runner
    return _windows_listeners(port, run) if os.name == "nt" else _posix_listeners(port, run)


def is_ours(listener: Listener) -> bool:
    """A listener this stack may stop: a known interpreter or kernel, never a browser."""
    name = (listener.name or "").strip().lower()
    if name in OURS:
        return True
    return name.startswith("python") or name.endswith("serveur_rag.py")


# --- planning --------------------------------------------------------------


def project_dir(start: Path | None = None) -> Path:
    """The directory holding ``docker-compose.yml``: the package's grandparent."""
    if start is not None:
        return Path(start)
    return Path(__file__).resolve().parents[2]


def compose_containers(*, runner: Runner | None = None, directory: Path | None = None) -> list[str]:
    """Names of this compose project's containers. Empty when Docker is absent."""
    run = runner or _default_runner
    code, out, _err = run(["docker", "compose", "ps", "--format", "{{.Name}}"], project_dir(directory))
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def plan(
    *,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    ports: tuple[int, ...] = STACK_PORTS,
    runner: Runner | None = None,
    lister: Lister | None = None,
) -> Plan:
    """What a shutdown would stop. Pure: no process is signalled, no container removed."""
    wanted = tuple(dict.fromkeys(targets))
    discovered = Plan(targets=wanted)
    if in_container():
        discovered.notes.append(
            "UHB runs inside a container: 'docker' and 'local' are unavailable from here"
        )
        return discovered
    if "docker" in wanted:
        discovered.containers = compose_containers(runner=runner)
    if "local" in wanted:
        for port in ports:
            for listener in (lister or (lambda p: listeners(p, runner=runner)))(port):
                if is_ours(listener) and listener.pid != os.getpid():
                    discovered.listeners.append(listener)
                elif listener.pid != os.getpid():
                    discovered.refused.append(listener)
    return discovered


# --- acting ----------------------------------------------------------------


def _alive(pid: int, *, runner: Runner) -> bool:
    if os.name == "nt":
        code, out, _err = runner(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], None)
        return code == 0 and f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_listener(
    listener: Listener,
    *,
    runner: Runner,
    sleeper: Callable[[float], None],
    signaller: Signaller | None = None,
) -> Step:
    """Ask nicely, wait :data:`GRACE_SECONDS`, then force. Verify, do not assume.

    On POSIX the signal is a real ``os.kill`` — which is why it is injectable. A
    test that fakes the process list but signals a real pid either kills something
    on the machine or fails on a pid that belongs to another process, and both
    happened in CI before this parameter existed.
    """
    label = f"{listener.name} (pid {listener.pid}) on port {listener.port}"
    send = signaller or os.kill
    if os.name == "nt":
        # No /F: a graceful request first, exactly like a window close.
        runner(["taskkill", "/PID", str(listener.pid), "/T"], None)
    else:
        try:
            send(listener.pid, 15)
        except OSError as exc:
            return Step("local", f"{label}: cannot signal ({type(exc).__name__})", False)
    waited = 0.0
    while waited < GRACE_SECONDS:
        sleeper(0.2)
        waited += 0.2
        if not _alive(listener.pid, runner=runner):
            return Step("local", f"{label}: stopped after {waited:.1f}s", True)
    if os.name == "nt":
        runner(["taskkill", "/F", "/T", "/PID", str(listener.pid)], None)
    else:
        try:
            send(listener.pid, 9)
        except OSError as exc:
            return Step("local", f"{label}: refused to die ({type(exc).__name__})", False)
    sleeper(0.2)
    still = _alive(listener.pid, runner=runner)
    return Step(
        "local",
        f"{label}: " + ("killed after the grace period" if not still else "STILL ALIVE"),
        not still,
    )


def stop_docker(*, runner: Runner | None = None, directory: Path | None = None) -> Step:
    """``docker compose down`` for this project, and nothing wider."""
    run = runner or _default_runner
    containers = compose_containers(runner=run, directory=directory)
    if not containers:
        return Step("docker", "no container is running for this project", True)
    code, out, err = run(
        ["docker", "compose", "down", "--timeout", "10"], project_dir(directory)
    )
    if code != 0:
        return Step("docker", f"docker compose down failed: {err or out or code}", False)
    left = compose_containers(runner=run, directory=directory)
    if left:
        return Step("docker", f"containers still present: {', '.join(left)}", False)
    return Step("docker", f"stopped {len(containers)} container(s)", True)


def stop_local(
    *,
    ports: tuple[int, ...] = STACK_PORTS,
    runner: Runner | None = None,
    lister: Lister | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    signaller: Signaller | None = None,
) -> list[Step]:
    """Stop every recognisable listener on our ports, politely then firmly."""
    discovered = plan(targets=("local",), ports=ports, runner=runner, lister=lister)
    steps = [
        _stop_listener(listener, runner=runner or _default_runner, sleeper=sleeper,
                       signaller=signaller)
        for listener in discovered.listeners
    ]
    steps += [
        Step("local", f"{item.name} (pid {item.pid}) on port {item.port}: left alone, "
                      "not a UHB process", False)
        for item in discovered.refused
    ]
    if not steps:
        steps.append(Step("local", "no listener on the stack ports", True))
    return steps


def shutdown(
    *,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    ports: tuple[int, ...] = STACK_PORTS,
    runner: Runner | None = None,
    lister: Lister | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the plan and report every step, including the ones that failed.

    ``self`` is not handled here: the caller owns the server socket, and it must
    answer before the socket goes away. The returned payload says so explicitly
    instead of pretending this function stopped the process that is running it.
    """
    wanted = tuple(dict.fromkeys(targets))
    unknown = [name for name in wanted if name not in TARGETS]
    if unknown:
        raise ShutdownError(f"unknown target(s): {', '.join(unknown)} (expected {', '.join(TARGETS)})")

    steps: list[Step] = []
    notes: list[str] = []
    if in_container():
        notes.append("inside a container: only 'self' can be stopped")
        wanted = tuple(name for name in wanted if name == "self")

    if "docker" in wanted:
        steps.append(stop_docker(runner=runner))
    if "local" in wanted:
        steps.extend(stop_local(ports=ports, runner=runner, lister=lister, sleeper=sleeper))

    return {
        "ok": all(step.ok for step in steps) or not steps,
        "targets": list(wanted),
        "steps": [step.to_dict() for step in steps],
        "self": "stopping after this response" if "self" in wanted else "kept running",
        "notes": notes,
    }


__all__ = [
    "DEFAULT_TARGETS",
    "GRACE_SECONDS",
    "OURS",
    "STACK_PORTS",
    "TARGETS",
    "TOKEN_ENV",
    "TOKEN_FILE_ENV",
    "TOKEN_HEADER",
    "Listener",
    "Plan",
    "ShutdownError",
    "Step",
    "compose_containers",
    "in_container",
    "is_ours",
    "listeners",
    "plan",
    "project_dir",
    "resolve_token",
    "shutdown",
    "stop_docker",
    "stop_local",
    "token_matches",
    "token_path",
]
