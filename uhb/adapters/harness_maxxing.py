"""Harness Maxxing adapter shape — the fake CLI, wired by one variable.

Harness Maxxing resolves its model binary from ``MAXXING_CLAUDE_BIN`` (measured in
``server/maxxing/sessions/spawn.py``) and speaks Claude Code's ``stream-json``. UHB
therefore presents itself as a ``claude`` binary: no patch, no fork, and no reuse of
the product's Fair Source code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BINARY_ENV = "MAXXING_CLAUDE_BIN"
MODULE = "uhb.entrypoints.fake_claude"


def launcher_source() -> str:
    """A tiny launcher that forwards every argument to the UHB fake CLI."""
    if os.name == "nt":
        return f'@echo off\r\n"{sys.executable}" -m {MODULE} %*\r\n'
    return f'#!/bin/sh\nexec "{sys.executable}" -m {MODULE} "$@"\n'


def install_launcher(target_dir: str | Path) -> Path:
    """Write the launcher and return its path. Idempotent."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    name = "uhb-claude.cmd" if os.name == "nt" else "uhb-claude"
    path = target / name
    path.write_text(launcher_source(), encoding="utf-8", newline="")
    if os.name != "nt":
        path.chmod(0o755)
    return path


def env_overrides(target_dir: str | Path | None = None) -> dict[str, str]:
    """The environment Harness Maxxing must be started with."""
    if target_dir is not None:
        binary = install_launcher(target_dir)
    else:
        binary = Path(sys.executable).with_name("uhb-claude")
    return {BINARY_ENV: str(binary)}


def argv_contract() -> dict[str, list[str]]:
    """What Harness Maxxing will pass, for documentation and tests."""
    return {
        "fresh_session": [
            "-p", "--output-format", "stream-json", "--verbose",
            "--model", "{model}", "--input-format", "stream-json",
            "--session-id", "{session_id}",
        ],
        "resume": [
            "-p", "--output-format", "stream-json", "--verbose",
            "--model", "{model}", "--input-format", "stream-json",
            "--resume", "{session_id}",
        ],
    }


def license_note() -> str:
    return (
        "Harness Maxxing is Fair Source, not MIT. The UHB fake CLI is an "
        "independent reimplementation of the stream-json contract; no code from "
        "harness-maxxing is copied, and fake_claude.py was studied, not reused."
    )
