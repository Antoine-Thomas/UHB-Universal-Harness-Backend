"""OpenClaw adapter shape — a registered CLI backend.

OpenClaw executes a turn through a local command when a model entry declares
``agentRuntime.id``; the backend is described by a ``CliBackendConfig`` passed to
``api.registerCliBackend`` (measured in ``docs/plugins/cli-backend-plugins.md``).

This module produces that config. The process it points at is the same binary as
the Harness Maxxing path: OpenClaw parses ``jsonlDialect: "claude-stream-json"``,
which is exactly what UHB already emits.
"""

from __future__ import annotations

from typing import Any

BACKEND_ID = "uhb-cli"
PROVIDER = "uhb"
MODULE = "uhb.entrypoints.fake_claude"


def cli_backend_config(command: str | None = None) -> dict[str, Any]:
    """The object a plugin hands to ``api.registerCliBackend``."""
    return {
        "id": BACKEND_ID,
        "modelProvider": PROVIDER,
        "config": {
            "command": command or "uhb-cli",
            "args": [
                "--output-format", "stream-json",
                "--input-format", "stream-json",
            ],
            "output": "jsonl",
            "jsonlDialect": "claude-stream-json",
            "input": "stdin",
            "sessionArgs": ["--session", "{sessionId}"],
            "sessionMode": "existing",
            "modelArg": "--model",
            "serialize": True,
        },
    }


def model_refs(models: list[str] | None = None) -> list[str]:
    """Model references the control plane should allow-list."""
    models = models or ["deepseek-flash", "deepseek-v4-pro"]
    return [f"{PROVIDER}/{model}" for model in models]


def config_json(command: str | None = None) -> str:
    """Same object, ready to paste into a plugin's registration call."""
    import json

    return json.dumps(cli_backend_config(command), indent=2)


def capabilities() -> dict[str, str]:
    """Which pivot functions each integration level covers (ADAPTERS.md §2)."""
    return {
        "cli_backend_v0_1": "execute_task, stream, cancel, health, resolve_provider",
        "agent_harness_v0_2": "+ sync_memory, load_skill, route_model",
    }
