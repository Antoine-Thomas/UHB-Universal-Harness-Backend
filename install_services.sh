#!/usr/bin/env bash
# UHB service bootstrap (Linux / macOS) — thin wrapper around `uhb.services.install`.
# The logic lives in Python, once: this script only finds an interpreter and hands over.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

PYTHON="${UHB_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "UHB needs Python 3.11 or newer on PATH (or set UHB_PYTHON)." >&2
  exit 1
fi

if ! "$PYTHON" -c 'import uhb' >/dev/null 2>&1; then
  echo "The uhb package is not importable from here." >&2
  echo "Install it first:  $PYTHON -m pip install -e ." >&2
  exit 1
fi

exec "$PYTHON" -m uhb.services.install "$@"
