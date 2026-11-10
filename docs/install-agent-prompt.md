# Install or update UHB — agent prompt

![Status](https://img.shields.io/badge/status-v0.3-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

A prompt to hand to a coding agent so it brings a UHB checkout to a working
`uhb` command, on Windows, macOS or Linux, without changing anything outside the
repository. Every path and number below comes from running the command on the
reference machine, not from what the layout was expected to be.

Paste the block between the `---` markers into the agent. Four facts make it
different from a generic install recipe:

**The checkout may not contain the code.** A commit predating the packaging
release carries `SPEC.md`, `ARCHITECTURE.md` and no `uhb/`, no `pyproject.toml`,
no `install.sh`. An install attempt then fails with `No such file or directory`,
whatever the command — so the presence of `install.sh` and `pyproject.toml` is
checked *before* anything is installed.

**`install.sh` trusts `$PYTHON`, and `python3` is not always a suitable one.**
`install.sh:51` reads `PYTHON="${PYTHON:-python3}"`. On a machine where `python3`
resolves to a harness venv, the project `.venv` is built from that interpreter
instead of a real CPython 3.11+. Pass `PYTHON` explicitly.

**There is no `.env` loader.** Configuration is `uhb/config.yaml`, or the path in
`$UHB_CONFIG`; credentials are read from the process environment by variable
name. A fabricated config path fails at the first call, not at install time.

**PATH edits are dangerous here.** The host harness is itself a PATH citizen: a
rewrite that reorders entries changes which binary the host resolves.

---

## The prompt

```text
Task: install or update UHB cleanly on Windows 10/11, macOS, or Linux, without
breaking the existing environment and without assuming the checkout is complete.

Repo    : Antoine-Thomas/UHB-Universal-Harness-Backend
Package : uhb-harness (PyPI) — the command is `uhb`
Requires: Python >= 3.11, git.

## 0. Environment detection (show raw output)
- OS: Windows (Git Bash or PowerShell), macOS, Linux.
- Python candidates >= 3.11 only. On Windows, `python3` may be a harness venv
  without pip — do NOT use it. Find a real Python 3.11+ and pass it explicitly to
  install.sh (see §2).
- Verify the candidate before trusting it:
    <python> -c "import sys; print(sys.version)"
    <python> -m pip --version
- No Python >= 3.11 available: STOP, install one (winget/brew/apt), rerun.

## 1. Repo state (clone, or fast-forward in place)
Set REPO = the target directory.

### Case A — REPO does not exist
  git clone --recurse-submodules <url> "$REPO"
  If the one-shot form fails with "not a git repository: /tmp/..." (a Git Bash
  submodule-helper quirk, not a repository problem):
  git clone <url> "$REPO" && git -C "$REPO" submodule update --init

### Case B — REPO exists (the common case)
- Do NOT use `gh repo clone` on an existing directory: it fails with
  "destination path already exists" and updates nothing.
- `git -C "$REPO" status --porcelain` — if non-empty, STOP and show the files.
  No stash, no reset, no checkout.
- `git -C "$REPO" fetch --all --prune`
- `git -C "$REPO" merge --ff-only origin/main`
  If the merge fails (divergence): STOP, do not rewrite history.
- Precondition: after the merge, `pyproject.toml` and `install.sh` MUST exist at
  HEAD. If either is missing, the checkout is a docs-only commit — STOP and
  report the commit hash. Do not try to install a package that is not there.
- If `.gitmodules` exists and `git -C "$REPO" submodule status` shows a leading
  `-`:
    git -C "$REPO" submodule update --init
  No `--recursive`: .gitmodules declares one level, uhb/services/mari/framework.
  The submodule is only needed to build the MARI bridge; the CLI and
  `uhb brain status` work without it.

## 2. Install with the correct interpreter
install.sh does `PYTHON="${PYTHON:-python3}"`. On Windows, `python3` may be a
harness venv without pip. Always pass PYTHON explicitly.

  Windows Git Bash:
    PYTHON="C:/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe" \
      ./install.sh --with-yaml
  macOS / Linux:
    PYTHON=python3.11 ./install.sh --with-yaml

Options read from install.sh (an unknown option exits 2 — do not invent one):
  --dry-run        print the commands, change nothing
  --with-yaml      also install pyyaml, needed by `uhb config set`
  --with-services  + SiYuan, downloaded and sha256-verified (~240 MB)
  --full           + SiYuan + RAG dependencies (~1.5 GB) + MARI bridge build
  -h, --help

Default for a first install: `--dry-run`, then `--with-yaml`. Do not use `--full`
unless the user explicitly asks for the full stack.

install.sh creates `.venv` inside the repo, never uses sudo, never writes outside
the checkout, and a second run is a no-op reinstall. Never install uhb-harness
into any other environment.

## 3. Verifications (raw output, no paraphrasing)
  .venv/Scripts/uhb.exe --version   (Windows)
  .venv/bin/uhb --version           (macOS / Linux)
  Expected: UHB (Universal Harness Backend) 0.1.2 (or newer)

  <uhb> status                 providers with loaded/MISSING, modes, repository
  <uhb> modes show review      "write": [] plus a deny list
  <uhb> brain status           ● or ○ per service, exit 0 even when one is down
  <uhb> docs                   path and URL of the embedded manual

Never print a credential: the report says `loaded` or `MISSING`, never a value.

## 4. Making `uhb` available in a fresh shell (do NOT modify PATH unasked)
Offer both options and wait for the user's choice.

### Option A — wrapper (recommended: non-intrusive, reversible)
- Windows needs TWO wrappers, one per shell family: `cmd.exe` and PowerShell run a
  `.cmd`, while Git Bash does not execute one — it needs a script with no
  extension plus a shebang. Both go in `%USERPROFILE%\.local\bin`, already in the
  User Path and already hosting wrappers:
      uhb.cmd -> @echo off
                 "<REPO>\.venv\Scripts\uhb.exe" %*
      uhb     -> #!/bin/sh
                 exec "/c/<REPO>/.venv/Scripts/uhb.exe" "$@"
  `where.exe uhb` finding the `.cmd` is NOT evidence that bash can run it: bash
  then fails with "command not found". Test both shells before declaring success.
  Write `uhb.cmd` with CRLF endings, and never with `printf`: printf consumes
  `\U` as an escape, so `\Users` inside the path is eaten and the file lands
  broken. Use a heredoc, or write the bytes — Python
  `Path(...).write_bytes(b'@echo off\r\n...')` — then confirm with `od -c`.
  Do NOT use `%LOCALAPPDATA%\Microsoft\WindowsApps`: a protected UWP alias
  folder, not a place to drop scripts.
- macOS / Linux: symlink into ~/.local/bin (add it to the Path only if absent):
      ln -s "$REPO/.venv/bin/uhb" "$HOME/.local/bin/uhb"

### Option B — append to the User Path (persistent)
- Windows: never `setx` (truncates at 1024 characters). Read the User value only,
  and append once:
      $venvScripts = "<REPO>\.venv\Scripts"
      $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
      if (($userPath -split ';') -notcontains $venvScripts) {
          [Environment]::SetEnvironmentVariable("Path", "$userPath;$venvScripts", "User")
      }
  Do NOT concatenate `$env:Path`: in a session it is User + Machine, so writing it
  back copies the Machine half into the User half.
- macOS / Linux: append to the profile actually in use (~/.zshrc or ~/.bashrc).

PATH guard — record, change, compare. Never assume an order:
- Before: `where.exe hermes` and `where.exe python` (Windows) /
  `command -v hermes` and `command -v python` (Unix).
- After: the same commands must return the same first entry.
- Different → STOP and show both outputs.

## 5. Credentials (only if real model calls are wanted)
UHB reads no `.env` file. Configuration is `uhb/config.yaml` next to the package,
or the path in `$UHB_CONFIG` (uhb/core/router.py:17). Keys come from the process
environment, under the variable names declared in that config — e.g.
DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, KIMI_API_KEY. Ollama needs
none. Export the variable in the shell that launches `uhb`; never echo a value.

## 6. Final report (concise, in the user's language)
- OS and interpreter used (path + version).
- HEAD before / after, when a fast-forward happened.
- `uhb --version` output.
- Providers: loaded / MISSING, no values.
- Brain state: rag / siyuan / mari, ● or ○.
- PATH option chosen (A or B), and confirmation that `where.exe hermes` and
  `where.exe python` are unchanged against the recorded baseline.
- Any error, with the exact command to reproduce it.

## Hard rules
- No sudo, no setx.
- No `git reset --hard`, no `--force`, no history rewrite, no silent stash.
- Do not install uhb-harness into any venv other than `$REPO/.venv`.
- No improvised workaround (DLL copy, a second unvetted Python, manual pip into a
  system interpreter). A failing step means STOP with the raw output.
```

---

## Why each guard exists

| Guard | Failure it prevents |
| --- | --- |
| Precondition on `pyproject.toml` + `install.sh` | installing from a documentation-only commit — the blocker actually seen on a clone 8 commits behind whose tree held no `uhb/` at all |
| `PYTHON=` passed explicitly | `.venv` built from a harness venv instead of CPython 3.11+ |
| `merge --ff-only`, never `reset` | matches `uhb update`, which has no `--force` and no `reset` |
| Wrapper before PATH edit | reordering the Path changes which `hermes` the host harness resolves |
| Two wrappers on Windows | `where.exe` reports the `.cmd`, and bash still answers "command not found" |
| `GetEnvironmentVariable("Path","User")` | `$env:Path` is User + Machine: writing it back duplicates the Machine half |
| Before/after PATH baseline | any statement about which directory "must stay first" is machine-specific and goes stale |
| No `.env` loader | the package has none; an invented config path fails at the first call |

## Notes from the reference run

- Windows 11, Git Bash, `gh` authenticated. `-C` paths must be written `C:/...`:
  git is a native binary and MSYS does not translate the arguments.
- `python3` resolved to a harness venv; the usable interpreter was
  `C:/Users/searc/AppData/Local/Programs/Python/Python311/python.exe` (3.11.9).
- Measured: 0.1.2 installed from `main@01c2436`; `uhb status` reported five
  providers and four modes; `uhb brain status` reported RAG, SiYuan and MARI all
  answering, exit 0; `uhb docs` exited 0 with both PDFs on disk.
- The clone arrived on a docs-only commit (`6741380`) and needed a fast-forward
  of 104 files / +13 179 lines before `install.sh` existed at all.
