"""``execute_code`` — run a Python snippet and bring back what it produced.

Why a tool and not a prompt: asked for a chart, a model writes matplotlib code
and stops there. The missing step is mechanical — write the code to a file, run
it, find the files it produced, hand the paths back — and a mechanical step
belongs in the backend.

What is guaranteed
------------------
* **The mode decides.** No mode, or a mode whose ``tools`` list does not carry
  ``execute_code``, and nothing runs: :class:`~uhb.hub.modes.ModeRequired` or
  :class:`~uhb.hub.modes.ModeViolation` is raised before a byte is written. A
  read-only mode therefore cannot execute — that is a check, not a convention.
* **The child gets a scrubbed environment.** An allow-list (``PATH``,
  ``SYSTEMROOT``, the temp directories) plus the run directory as ``HOME``.
  Every API key present in the operator's shell is dropped, so a snippet cannot
  read ``DEEPSEEK_API_KEY`` and print it into a transcript.
* **Output is capped, never silently.** A truncated stream says how much was
  dropped, and the result's ``notes`` say why each image was left out.
* **A timeout kills the process tree**, not just the parent.
* **Images are discovered, not declared.** Any image file the snippet creates or
  rewrites inside its run directory is reported with its path, MIME type, size,
  and optionally a ``data:`` URL a UI can put straight into ``<img src>``.

What is NOT guaranteed — read this before exposing the tool
-----------------------------------------------------------
This is containment, not a security boundary. The snippet runs as the operator's
user with the operator's filesystem, on purpose: the ordinary use is "plot my
data" or "check this regex". It is not a sandbox for hostile code, it does not
seal the network, and on Windows the resource limits below do not exist at all —
only the timeout does. Do not publish the HTTP route beyond loopback.
"""

from __future__ import annotations

import base64
import math
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..hub.modes import ModeRequired, ModeSpec, ModeViolation, get_mode

#: Name a mode must list in ``tools`` for this module to run at all.
TOOL_NAME = "execute_code"

DEFAULT_TIMEOUT = 30.0
#: A snippet that needs more than this is a batch job, not a tool call.
MAX_TIMEOUT = 300.0
MIN_TIMEOUT = 0.5
#: Characters kept per stream (stdout, stderr separately).
MAX_OUTPUT = 20_000
#: A snippet is a snippet: past this, it is a file to run by hand.
MAX_CODE_CHARS = 200_000
#: Above this, the image is described but not inlined as base64.
MAX_INLINE_BYTES = 1_500_000
#: A single image larger than this is not reported at all (size only).
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 8

#: Where run directories go: ``$UHB_RUNS_DIR`` or ``~/.uhb/runs``.
RUNS_ENV = "UHB_RUNS_DIR"

#: Extensions that mean "this is something a UI can display".
IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

#: Everything the child inherits. Deliberately short: no ``*_API_KEY``, no
#: ``UHB_*`` credential, nothing that names a provider.
ENV_ALLOWED: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)

#: Files of the harness itself, never reported as artefacts.
HARNESS_FILES = ("code.py", "_uhb_runner.py")


class CodeExecutionError(RuntimeError):
    """The call itself is wrong: empty code, bad timeout, unusable directory."""


@dataclass(frozen=True, slots=True)
class ImageArtifact:
    """One image the snippet produced."""

    path: Path
    relative: str
    mime: str
    size: int
    data_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "relative": self.relative,
            "mime": self.mime,
            "bytes": self.size,
            # None means "not inlined" — the reason is in the result's notes.
            "data_url": self.data_url,
        }


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What a snippet did, including what it could not tell us."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    run_dir: Path
    images: tuple[ImageArtifact, ...] = ()
    #: Every caveat, in plain words: truncation, skipped image, dropped base64.
    notes: tuple[str, ...] = ()
    timeout_s: float = DEFAULT_TIMEOUT
    mode: str = ""
    command: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_s": self.duration_s,
            "timeout_s": self.timeout_s,
            "mode": self.mode,
            "run_dir": str(self.run_dir),
            "command": list(self.command),
            "images": [image.to_dict() for image in self.images],
            "notes": list(self.notes),
        }


# --- the gate --------------------------------------------------------------


def _spec(mode: ModeSpec | str | None) -> ModeSpec:
    """Resolve the envelope and require the tool, or raise before running.

    Mirrors :func:`uhb.hub.guard.commit`: no mode is not "the default mode", it
    is a hole — and an execution hole is worse than a write hole.
    """
    if mode is None:
        raise ModeRequired(
            "execute_code needs a mode: pass one (get_mode('dev'), or a profile of "
            "your own whose 'tools' list carries 'execute_code')"
        )
    spec = get_mode(mode) if isinstance(mode, str) else mode
    spec.check_tool(TOOL_NAME)
    return spec


# --- places ----------------------------------------------------------------


def runs_root() -> Path:
    """Directory holding one subdirectory per run. Created on demand."""
    configured = os.environ.get(RUNS_ENV, "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".uhb" / "runs"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CodeExecutionError(f"cannot create the runs directory {base}: {exc}") from exc
    return base


def _new_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return runs_root() / f"run-{stamp}-{uuid.uuid4().hex[:6]}"


def _prepare_workdir(workdir: str | Path | None) -> Path:
    target = Path(workdir).expanduser() if workdir is not None else _new_run_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CodeExecutionError(f"cannot use the working directory {target}: {exc}") from exc
    if not target.is_dir():
        raise CodeExecutionError(f"the working directory is not a directory: {target}")
    return target.resolve()


def _child_env(run_dir: Path) -> dict[str, str]:
    """The allow-listed environment, with the run directory as home.

    ``MPLBACKEND=Agg`` is what makes ``savefig`` work with no display, which is
    the whole point on a headless server. ``MPLCONFIGDIR`` stays inside the run
    so matplotlib's font cache never lands in the operator's profile.
    """
    env = {name: os.environ[name] for name in ENV_ALLOWED if name in os.environ}
    env.update(
        {
            "HOME": str(run_dir),
            "USERPROFILE": str(run_dir),
            "TMPDIR": str(run_dir),
            "TMP": str(run_dir),
            "TEMP": str(run_dir),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(run_dir / ".mpl"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UHB_RUN_DIR": str(run_dir),
        }
    )
    return env


# --- the child -------------------------------------------------------------

#: Harness executed by the child: set limits, then run ``code.py`` as __main__.
#: The limits live in the child on purpose — ``preexec_fn`` is unsafe in a
#: threaded server (this one is: ``ThreadingHTTPServer``).
_RUNNER = '''\
"""UHB run harness — sets resource limits, then executes code.py."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
os.makedirs(os.path.join(ROOT, ".mpl"), exist_ok=True)

try:  # POSIX only: on Windows the module does not exist, and timeout is all we have
    import resource
except ImportError:
    resource = None

if resource is not None:
    for name, value in (("RLIMIT_CPU", __CPU__), ("RLIMIT_AS", __AS__),
                        ("RLIMIT_FSIZE", __FSIZE__), ("RLIMIT_NOFILE", __NOFILE__)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass

import runpy

runpy.run_path(os.path.join(ROOT, "code.py"), run_name="__main__")
'''


def _runner_source(timeout: float) -> str:
    cpu = max(1, int(math.ceil(timeout)) + 1)
    return (
        _RUNNER.replace("__CPU__", str(cpu))
        .replace("__AS__", str(2 * 1024**3))
        .replace("__FSIZE__", str(64 * 1024**2))
        .replace("__NOFILE__", "256")
    )


def _python() -> str:
    # sys.executable is the interpreter that imported uhb: no PATH lookup, so a
    # different python on PATH cannot silently run the snippet.
    return sys.executable or "python"


# --- discovery -------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """``{relative path: (mtime_ns, size)}`` for every file under ``root``."""
    seen: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        seen[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return seen


def _is_harness_file(relative: str) -> bool:
    return relative in HARNESS_FILES or relative.startswith(".mpl/")


def _discover_images(
    run_dir: Path,
    before: dict[str, tuple[int, int]],
    *,
    include_base64: bool,
    notes: list[str],
) -> tuple[ImageArtifact, ...]:
    """Images created or rewritten by this run, in modification order."""
    after = _snapshot(run_dir)
    changed = [
        relative
        for relative, signature in after.items()
        if not _is_harness_file(relative) and before.get(relative) != signature
    ]
    candidates: list[tuple[int, str]] = []
    for relative in changed:
        suffix = Path(relative).suffix.lower()
        if suffix in IMAGE_MIME:
            candidates.append((after[relative][0], relative))

    if not candidates:
        return ()
    candidates.sort()
    if len(candidates) > MAX_IMAGES:
        notes.append(
            f"{len(candidates) - MAX_IMAGES} image(s) beyond the first {MAX_IMAGES} "
            f"were not reported: {', '.join(rel for _mtime, rel in candidates[MAX_IMAGES:])}"
        )
        candidates = candidates[:MAX_IMAGES]

    found: list[ImageArtifact] = []
    for _mtime, relative in candidates:
        path = run_dir / relative
        suffix = path.suffix.lower()
        mime = IMAGE_MIME[suffix]
        try:
            size = path.stat().st_size
        except OSError as exc:
            notes.append(f"{relative}: not readable ({type(exc).__name__}), not reported")
            continue
        if size > MAX_IMAGE_BYTES:
            notes.append(
                f"{relative}: {size} bytes exceeds the {MAX_IMAGE_BYTES}-byte limit, "
                "not reported"
            )
            continue
        data_url = None
        if include_base64:
            if size <= MAX_INLINE_BYTES:
                try:
                    payload = base64.b64encode(path.read_bytes()).decode("ascii")
                except OSError as exc:
                    notes.append(f"{relative}: base64 skipped ({type(exc).__name__})")
                else:
                    data_url = f"data:{mime};base64,{payload}"
            else:
                notes.append(
                    f"{relative}: {size} bytes exceeds the {MAX_INLINE_BYTES}-byte inline "
                    "limit — read the file from its path instead"
                )
        found.append(
            ImageArtifact(path=path, relative=relative, mime=mime, size=size, data_url=data_url)
        )
    return tuple(found)


# --- process control -------------------------------------------------------


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the snippet *and* anything it spawned."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return
    try:
        os.killpg(os.getpgid(process.pid), 9)
    except (OSError, ProcessLookupError):
        process.kill()


def _clip(text: str, limit: int, label: str, notes: list[str]) -> str:
    """Cut at ``limit`` and say so — a silent cut reads as "the code printed that"."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    notes.append(f"{label} truncated: {dropped} character(s) dropped (limit {limit})")
    return text[:limit] + f"\n… [{label} truncated: {dropped} character(s) dropped]"


# --- the tool --------------------------------------------------------------


def execute_code(
    code: str,
    *,
    mode: ModeSpec | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    workdir: str | Path | None = None,
    include_base64: bool = False,
    max_output: int = MAX_OUTPUT,
) -> ExecutionResult:
    """Run ``code`` in its own directory and report streams, code and images.

    ``mode`` is mandatory and must list ``execute_code`` in its ``tools``: this
    function refuses to run on behalf of an envelope that has no permission to
    do so. ``workdir`` defaults to a fresh ``~/.uhb/runs/run-…`` directory, which
    is kept: the image paths have to outlive the call for a UI to read them.
    """
    spec = _spec(mode)

    if not isinstance(code, str) or not code.strip():
        raise CodeExecutionError("code must be a non-empty string")
    if len(code) > MAX_CODE_CHARS:
        raise CodeExecutionError(
            f"code is {len(code)} characters; the limit is {MAX_CODE_CHARS} — "
            "write it to a file and run it with your own tooling"
        )
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise CodeExecutionError(f"timeout must be a number, got {timeout!r}") from exc
    if not timeout >= MIN_TIMEOUT:
        raise CodeExecutionError(f"timeout must be at least {MIN_TIMEOUT}s, got {timeout}")
    timeout = min(timeout, MAX_TIMEOUT)

    run_dir = _prepare_workdir(workdir)
    before = _snapshot(run_dir)
    notes: list[str] = []

    (run_dir / "code.py").write_text(code, encoding="utf-8", newline="\n")
    (run_dir / "_uhb_runner.py").write_text(_runner_source(timeout), encoding="utf-8", newline="\n")

    command = (_python(), "-I", "-B", "-X", "utf8", str(run_dir / "_uhb_runner.py"))
    started = time.monotonic()
    timed_out = False
    stdout = stderr = ""
    exit_code = -1

    try:
        process = subprocess.Popen(
            command,
            cwd=str(run_dir),
            env=_child_env(run_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
            ),
        )
    except OSError as exc:
        raise CodeExecutionError(f"cannot start the interpreter ({_python()}): {exc}") from exc

    try:
        out_bytes, err_bytes = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        out_bytes, err_bytes = process.communicate()
        notes.append(f"timed out after {timeout}s: the process tree was killed")
    duration = round(time.monotonic() - started, 3)
    exit_code = process.returncode if process.returncode is not None else -1

    # -X utf8 and PYTHONIOENCODING are set for the child, so decoding is not a guess.
    stdout = _clip(out_bytes.decode("utf-8", "replace"), max_output, "stdout", notes)
    stderr = _clip(err_bytes.decode("utf-8", "replace"), max_output, "stderr", notes)

    images = _discover_images(run_dir, before, include_base64=include_base64, notes=notes)

    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_s=duration,
        run_dir=run_dir,
        images=images,
        notes=tuple(notes),
        timeout_s=timeout,
        mode=spec.name,
        command=command,
    )


__all__ = [
    "CodeExecutionError",
    "DEFAULT_TIMEOUT",
    "ExecutionResult",
    "IMAGE_MIME",
    "ImageArtifact",
    "MAX_CODE_CHARS",
    "MAX_INLINE_BYTES",
    "MAX_OUTPUT",
    "MAX_TIMEOUT",
    "RUNS_ENV",
    "TOOL_NAME",
    "execute_code",
    "runs_root",
]
