"""The `hermes` entry point is a ``hermes.cmd`` shim on Windows.

``asyncio.create_subprocess_exec`` does not apply PATHEXT the way a shell does,
so the bare name raised FileNotFoundError even though the command works on the
prompt. These tests pin the two halves of the fix: the adapter hands
create_subprocess_exec the path ``shutil.which`` returned, and it keeps the bare
name — hence the same "binary not found" event — when nothing is on PATH. No
test starts a real harness: ``create_subprocess_exec`` is replaced by a spy, so
the assertion is about the argv UHB builds.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from uhb.adapters.hermes import HermesAdapter
from uhb.core.router import Router
from uhb.core.types import TaskContext

WINDOWS = sys.platform == "win32"


# --- doubles ---------------------------------------------------------------


class _FakeStream:
    """Just enough of a pipe: an async iterator over already-decoded lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self):
        async def generate():
            for line in self._lines:
                yield line

        return generate()


class _FakeProc:
    """A child that answered immediately: ``stdout`` yields, ``wait`` returns 0."""

    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.stdout = _FakeStream(lines if lines is not None else [b"hello from the shim\n"])
        self.stderr = None
        self.returncode: int | None = None
        self.terminated = False

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True


async def _collect(adapter: HermesAdapter, context: TaskContext) -> list:
    return [event async for event in adapter.execute_task("dis bonjour", context)]


def _install_shim(directory: Path) -> Path:
    """A ``hermes`` a shell can run: ``.cmd`` on Windows, an executable elsewhere."""
    if WINDOWS:
        shim = directory / "hermes.cmd"
        shim.write_text("@echo off\r\necho hello from the shim\r\n", encoding="utf-8")
    else:
        shim = directory / "hermes"
        shim.write_text("#!/bin/sh\necho hello from the shim\n", encoding="utf-8")
        shim.chmod(0o755)
    return shim


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(str(right)))


# --- the fix ---------------------------------------------------------------


def test_which_resolves_the_shim_on_the_injected_path(tmp_path, monkeypatch):
    shim = _install_shim(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    found = shutil.which("hermes")
    assert found is not None, "the shim is not on PATH: the rest of this test proves nothing"
    assert _same_path(found, shim)
    if WINDOWS:
        assert found.lower().endswith(".cmd"), "PATHEXT must supply the extension"


def test_execute_task_spawns_the_resolved_path(tmp_path, monkeypatch):
    shim = _install_shim(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    calls: list[tuple[tuple, dict]] = []

    async def spy_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_exec)
    adapter = HermesAdapter(binary="hermes", mode="dev", router=Router())
    events = asyncio.run(_collect(adapter, TaskContext(workspace=str(tmp_path))))

    assert len(calls) == 1, "the adapter must spawn exactly one child"
    args, kwargs = calls[0]
    assert _same_path(args[0], shim), "argv[0] must be the path shutil.which returned"
    assert args[1:] == ("-p", "default", "-z", "dis bonjour", "--in", str(tmp_path)), (
        "arguments are untouched"
    )
    assert kwargs["cwd"] == str(tmp_path)
    assert "shell" not in kwargs, "resolution must not need shell=True"
    assert [event.type for event in events] == ["token"]
    assert events[0].content == "hello from the shim"


def test_unknown_binary_keeps_the_bare_name_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty: nothing named there
    assert shutil.which("uhb-no-such-harness") is None

    calls: list[tuple] = []

    async def failing_exec(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec)
    adapter = HermesAdapter(binary="uhb-no-such-harness", mode="dev", router=Router())
    events = asyncio.run(_collect(adapter, TaskContext(workspace=str(tmp_path))))

    assert calls[0][0] == "uhb-no-such-harness", "no resolution: the name is passed as given"
    assert [event.type for event in events] == ["error"]
    assert "binary not found" in events[0].content
    assert events[0].metadata["code"] == "FileNotFoundError"


@pytest.mark.skipif(not WINDOWS, reason="a .cmd shim only resolves on Windows")
def test_the_real_spawn_launches_a_cmd_shim(tmp_path, monkeypatch):
    """End to end, without the spy: the .cmd path is what makes the child run."""
    shim = _install_shim(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    adapter = HermesAdapter(binary="hermes", mode="dev", router=Router())
    events = asyncio.run(_collect(adapter, TaskContext(workspace=str(tmp_path))))

    assert events and events[0].type == "token"
    assert events[0].content == "hello from the shim"
    assert shim.is_file()
