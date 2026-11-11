"""`uhb ui` prints "→" and French text: a cp1252 console used to kill it.

A Windows console defaults to cp1252/cp850, so the very first message with an
arrow or an accent raised UnicodeEncodeError and the command died with a
traceback. ``cli.main`` now reconfigures stdout/stderr to UTF-8 before anything
is printed.

The reproduction needs the buggy default, which is Windows-only: elsewhere the
streams are already UTF-8 and the test would prove nothing, so it skips. The
last test runs everywhere and pins the reconfigure contract itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from uhb.entrypoints import cli

WINDOWS = sys.platform == "win32"
ROOT = Path(__file__).resolve().parents[2]


def _run_ui(**env_overrides: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    # `-X utf8=0` and PYTHONUTF8=0 so PYTHONIOENCODING is what decides.
    return subprocess.run(
        [sys.executable, "-X", "utf8=0", "-m", "uhb.entrypoints.cli", "ui"],
        cwd=ROOT,
        capture_output=True,
        env=env,
        timeout=120,
    )


@pytest.mark.skipif(not WINDOWS, reason="cp1252 console: the bug is Windows-only")
def test_ui_survives_a_cp1252_console():
    proc = _run_ui(PYTHONIOENCODING="cp1252:strict", PYTHONUTF8="0")

    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"uhb ui exited {proc.returncode}:\n{proc.stdout.decode(errors='replace')}\n{stderr}"
    assert "UnicodeEncodeError" not in stderr
    # The arrow did reach the console: the message was printed, not swallowed.
    assert "→" in proc.stdout.decode("utf-8", errors="replace")


@pytest.mark.skipif(not WINDOWS, reason="cp1252 console: the bug is Windows-only")
def test_status_survives_a_cp1252_console():
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="cp1252:strict", PYTHONUTF8="0")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-m", "uhb.entrypoints.cli", "status"],
        cwd=ROOT,
        capture_output=True,
        env=env,
        timeout=120,
    )
    assert "UnicodeEncodeError" not in proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0


def test_ensure_utf8_streams_reconfigures_every_text_stream(monkeypatch):
    """Platform-independent: the helper asks both streams for UTF-8, and a
    stream without reconfigure (or one that refuses) must not raise."""

    calls: list[dict] = []

    class _Stream:
        def __init__(self, refuse: bool = False) -> None:
            self.refuse = refuse

        def reconfigure(self, **kwargs) -> None:
            if self.refuse:
                raise OSError("closed")
            calls.append(kwargs)

    out, err = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._ensure_utf8_streams()
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2

    calls.clear()
    monkeypatch.setattr(sys, "stdout", _Stream(refuse=True))
    monkeypatch.setattr(sys, "stderr", object())  # no reconfigure at all
    cli._ensure_utf8_streams()  # neither branch may raise
    assert calls == []
