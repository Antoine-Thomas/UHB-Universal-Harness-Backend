"""Tools an agent turn may call, gated by the mode.

A tool here is a plain function with a typed result — no agent loop, no prompt,
no model. The mode's ``tools`` list is the only permission check, and it is the
same check every other act goes through, so ``GET /modes`` already tells a client
which tools exist before it asks for one.

    from uhb.tools import execute_code

    result = execute_code("print(2 + 2)", mode="dev")
    result.stdout        # '4\n'
    result.images        # () — nothing was drawn

``execute_code`` is the first tool: the missing step between "here is the
matplotlib code" and "here is the chart".
"""

from __future__ import annotations

from .execute import (
    DEFAULT_TIMEOUT,
    MAX_CODE_CHARS,
    MAX_INLINE_BYTES,
    MAX_OUTPUT,
    MAX_TIMEOUT,
    RUNS_ENV,
    TOOL_NAME,
    CodeExecutionError,
    ExecutionResult,
    ImageArtifact,
    execute_code,
    runs_root,
)

__all__ = [
    "CodeExecutionError",
    "DEFAULT_TIMEOUT",
    "ExecutionResult",
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
