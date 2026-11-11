#!/usr/bin/env bash
# UHB — Universal Harness Backend :: installer
#
# What it does, in order:
#   1. checks Python >= 3.11
#   2. creates .venv next to this script (never touches the system Python)
#   3. installs the package from this checkout, or from the git remote
#   4. verifies the `uhb` console script answers
#
# It never uses sudo, never writes outside this directory, and never edits a file
# you already have: a second run is a no-op reinstall.
#
#   ./install.sh                     # install from the current checkout
#   ./install.sh --with-yaml         # also install pyyaml (needed by `uhb config set`)
#   ./install.sh --with-services     # + SiYuan, downloaded and sha256-verified (~240 MB)
#   ./install.sh --full              # + SiYuan, RAG dependencies (~1.5 GB) and the MARI bridge
#   ./install.sh --python PATH       # interpreter to build .venv with (same as $PYTHON)
#   ./install.sh --dry-run           # print the commands, change nothing
#   ./install.sh --help

set -euo pipefail

PYTHON_MIN="3.11"
VENV_DIR=".venv"
EXTRA=""
DRY_RUN=0
WITH_SERVICES=0
FULL_STACK=0

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --with-yaml) EXTRA="[yaml]" ;;
    --with-services) WITH_SERVICES=1 ;;
    --full) WITH_SERVICES=1 ; FULL_STACK=1 ;;
    --python)
      shift
      if [ $# -eq 0 ]; then echo "error: --python needs a path (try --help)" >&2 ; exit 2 ; fi
      PYTHON="$1"
      ;;
    --python=*) PYTHON="${1#--python=}" ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY_RUN" -eq 1 ]; then say "+ $*"; else say "+ $*"; "$@"; fi
}

# --- 1. interpreter -------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  say "error: $PYTHON not found. Install Python ${PYTHON_MIN}+ and retry." >&2
  exit 1
fi

if ! "$PYTHON" - <<'PY'
import sys
sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
then
  say "error: Python ${PYTHON_MIN}+ required, found $("$PYTHON" --version 2>&1)" >&2
  exit 1
fi

say "UHB installer — $("$PYTHON" --version 2>&1)"

# A version is not enough: a venv built from another venv inherits its broken
# pip, and the failure surfaces much later as "No module named pip". Ask the
# interpreter to prove it can install before creating anything.
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
  if ! "$PYTHON" -m ensurepip --version >/dev/null 2>&1; then
    RESOLVED="$(command -v "$PYTHON" 2>/dev/null || printf '%s' "$PYTHON")"
    say "error: $("$PYTHON" --version 2>&1) at $RESOLVED cannot install packages: no pip, no ensurepip." >&2
    say "       a venv created by this script would be born broken." >&2
    say "       pass a standalone interpreter, for example:" >&2
    say "         py -3.11 -V                  (Windows launcher)" >&2
    say "         where.exe python3.11         (Windows)" >&2
    say "         /usr/bin/python3.11          (Unix)" >&2
    say "       then: PYTHON=<that path> ./install.sh   (or --python <that path>)" >&2
    exit 1
  fi
fi

# --- 2. virtual environment ----------------------------------------------
# A git worktree without pyproject.toml is an earlier commit, not a package
# source: installing from the remote there would run code the operator never
# checked out and report success for it. Refuse before creating anything.
if [ ! -f pyproject.toml ] && git rev-parse --git-dir >/dev/null 2>&1; then
  say "error: stale checkout: HEAD $(git rev-parse --short HEAD 2>/dev/null || echo unknown) has no pyproject.toml — run" >&2
  say "       git merge --ff-only origin/main, then re-run" >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  run "$PYTHON" -m venv "$VENV_DIR"
else
  say "· reusing existing $VENV_DIR"
fi

if [ -x "$VENV_DIR/bin/python" ]; then
  VENV_PY="$VENV_DIR/bin/python"
else
  VENV_PY="$VENV_DIR/Scripts/python.exe"   # Windows layout
fi

# --- 3. the package -------------------------------------------------------
run "$VENV_PY" -m pip install --quiet --upgrade pip
if [ -f pyproject.toml ]; then
  run "$VENV_PY" -m pip install --quiet -e ".${EXTRA}"
else
  say "no pyproject.toml here: installing from the git remote instead"
  run "$VENV_PY" -m pip install --quiet "uhb${EXTRA} @ git+https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend.git"
fi

# --- 4. verify ------------------------------------------------------------
UHB_BIN="$VENV_DIR/bin/uhb"
[ -x "$UHB_BIN" ] || UHB_BIN="$VENV_DIR/Scripts/uhb.exe"
if [ "$DRY_RUN" -eq 1 ]; then
  say "would verify: $UHB_BIN --version"
fi
if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$UHB_BIN" ]; then
  say "error: the console script was not created at $UHB_BIN" >&2
  exit 1
fi

# --- 5. optional second brain --------------------------------------------
# Nothing here is needed for UHB to run: modes, router, wiki and the panel work
# without any of it. This step is opt-in because it downloads hundreds of MB.
if [ "$WITH_SERVICES" -eq 1 ]; then
  say ""
  say "· second brain: SiYuan (official release, sha256 checked against SHA256SUMS.txt)"
  if [ "$FULL_STACK" -eq 1 ]; then
    say "· second brain: RAG dependencies (~1.5 GB: torch, faiss, flashrank) — long step"
    run "$VENV_PY" -m uhb.services.install --with siyuan,rag --yes
  else
    run "$VENV_PY" -m uhb.services.install --with siyuan
  fi

  if [ -f uhb/services/mari/bridge/Bridge.csproj ] && [ -d uhb/services/mari/framework/src ]; then
    if command -v dotnet >/dev/null 2>&1; then
      say "· MARI bridge: building from uhb/services/mari/bridge"
      run "$VENV_PY" -m uhb.services.install --with mari
    else
      say "· MARI bridge: skipped, the .NET SDK is not on PATH"
    fi
  else
    say "· MARI bridge: skipped, run 'git submodule update --init' first"
  fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
  say ""
  say "dry-run: nothing installed."
  exit 0
fi

say ""
say "installed: $("$UHB_BIN" --version)"
say ""
say "next:"
say "  $UHB_BIN status"
say "  $UHB_BIN modes list"
say "  $UHB_BIN run --mode dev --port 8080"
say ""
say "add it to your PATH for this shell:"
say "  export PATH=\"\$PWD/$VENV_DIR/bin:\$PATH\""
