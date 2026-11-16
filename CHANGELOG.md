# Changelog

All notable changes to UHB (the `uhb-harness` distribution, CLI `uhb`) are
recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with one deliberate caveat: **before 1.0.0 the HTTP surface is not frozen**.
After 1.0.0, a removed or renamed route is a major version.

Versions are read from `uhb/__init__.py` by `pyproject.toml`
(`dynamic = ["version"]`), so a release is one edit, one tag, one GitHub release.

## [Unreleased] — target 1.0.0

### Added

- `POST /shutdown` and a **power button** above the `9+ UHB` badge: the stack can
  now be stopped from the interface instead of from a terminal. Three targets —
  `docker` (`compose down` for this project only), `local` (listeners on 8101 /
  8200 / 6806 recognised as ours: polite stop, then a forced kill after 5 s,
  anything else reported and left alone) and `self` (stopped after the `202` is
  flushed, so the UI reads "stopping", never a reset). Gates: a token
  (`UHB_SHUTDOWN_TOKEN` or `~/.uhb/shutdown-token`), `{"confirm": true}`, and a
  closed target list; `dry_run` returns the plan without touching anything. The
  button is absent from `uhb ui --markup` unless `--shutdown` is asked for, and
  the markup never carries the token.
- `execute_code`, the first tool in `uhb/tools/`: it writes a Python snippet to
  its own run directory, executes it with the interpreter that hosts UHB, and
  reports the streams, the exit code and **the images the snippet produced** —
  path, MIME, size, and an optional `data:` URL for a UI to display. Images are
  discovered by snapshotting the directory, so `savefig("figs/plot.png")` is
  found without declaring anything.
- `POST /tools/execute` and `uhb tools exec`: the same tool over HTTP and on the
  command line. The mode is the gate — `execute_code` must be in the mode's
  `tools` list, `dev` carries it, and the route refuses a body that names another
  mode than the server's own, so `--mode chat` cannot be escalated by a request.
  The child gets an allow-listed environment (no provider key reaches a
  transcript), `MPLBACKEND=Agg`, a timeout that kills the process tree, and
  truncated streams that say how much was dropped.
- `GET /modes`: the permissions of each mode as data, so a client can choose one
  before spending a turn.
- `POST /route`: the plan (complexity, signals, ordered model chain, reason)
  without running anything — no adapter, no network, no token.
- Memory routes: `GET /rag/status`, `POST /rag/reindex`, `GET /siyuan/blocks`.
  `reindex` is honest about being a reload; a missing SiYuan token is a 400, not
  an empty list.
- Free tier: six free candidates from two OpenAI-compatible providers, a
  `free_first` mode policy, `routing.fallback`, and `routing.allow_paid` with
  `PaidModelRefused` when nothing free is left.
- `UHB_RAG_URL`: the RAG service address is read at call time, so a container can
  name the sibling service (`http://rag:8200`) instead of `127.0.0.1:8200`.
- An end-to-end proof of `docker-compose.yml` (three services, `docker compose
  down` clean) and a proof page for the `9+ UHB` widget (`test_ui.html`, generated
  from `uhb ui --markup` by `tools/make_test_ui.py`).
- This changelog.

### Fixed

- The CORS preflight advertised `GET` only, so any interface on another loopback
  port was blocked by the browser before it could call a POST route — measured
  with the power button, which reported a network failure on a healthy server.
  It now allows `POST` and the `x-uhb-token` header, still for loopback and
  `null` origins only; the token, not the preflight, is what authorises a stop.
- `shutdown` decoded `tasklist`/`netstat` output as UTF-8, which killed its reader
  thread on a French Windows (cp850) and left the local branch crashing instead
  of stopping the process. It now decodes cp850/cp1252 too.
- The RAG service refused to start without `manifeste.json`, so the `rag`
  container could not run while its own README promised `fragments: 0`. It now
  starts, `/sante` answers `200 {"status": "sans_index", "fragments": 0}`, and
  `/search` answers 503 — never an empty result set that would read as "your
  brain contains nothing".
- `POST /v1/messages` and `/v1/chat/completions` refuse a cross-origin browser
  POST (403) and a non-JSON body (415) before reading a byte.
- The container's routing table resolved every catalog entry, so the image no
  longer boots with no routing policy at all.
- The server runs on Python 3.10, as `uh web` does — `datetime.UTC` is not used.
- Windows: UTF-8 console, and a `.cmd` shim found through `shutil.which()` before
  `asyncio.create_subprocess_exec`.
- The installer requires `pip` and refuses a stale checkout.

### Changed

- CI runs on `ubuntu-latest`, `macos-latest` and `windows-latest` (Python
  3.10–3.13); the matrix is the source of truth for test counts.

## [0.1.2] — 2026-11-10

### Fixed

- Tests read `uhb.__version__` instead of hardcoding it, so a version bump can no
  longer break CI.

## [0.1.1] — 2026-11-10

### Added

- PyPI trusted publishing: a GitHub release builds the distribution and uploads
  it, after running the test suite.

### Changed

- Copyright holder aligned on the legal name (Thomas Leroyer, Antoine-Thomas on
  GitHub) in both licence texts.

## [0.1.0] — 2026-11-10

First reference implementation, shipped next to the MIT specification.

### Added

- The hub: `wiki` (`raw/` → `wiki/` with a schema, `ingest` / `query` / `lint`),
  `modes` (four profiles, fail-closed permissions, a prompt envelope), `router`
  (catalog, classification, tiered chain, fallback, measured latency), and
  `guard` (the gate on every write path).
- The contract: eight pivot functions in `core/contract.py` and their typed
  payloads, so `import uhb` never opens a socket or reads a file.
- Entrypoints: `uhb` CLI, the HTTP server (`/v1/messages`,
  `/v1/chat/completions`, `/status`, panel, manual download), `fake_claude`.
- Adapters: `hermes`, `harness_maxxing`, `openclaw`, and a read-only
  `second_brain` client for the local RAG, SiYuan and MARI bridge.
- Packaging: `uhb-harness` on PyPI, `install.sh`, service installers, French and
  English manuals, and the `9+ UHB` widget (`uhb ui --markup`).
- Containers: `docker/Dockerfile.uhb`, `docker/Dockerfile.rag`, and
  `docker-compose.yml` for the backend plus its optional second brain.

[Unreleased]: https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend/releases/tag/v0.1.0
