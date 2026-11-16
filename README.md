# UHB — Universal Harness Backend

> **Your harness gives you the body — the UI, the sessions, the automations.
> UHB gives it the brain — the memory, the routing, the absolute permissions.**

UHB is not another agent. It is the execution backend underneath one: modes that
decide what a turn may do, a router that decides which model answers, a write gate
that refuses before it touches a file, and a compiled wiki that remembers.

Harness Maxxing, OpenClaw, Hermes Agent or your own CLI plug into it — and stop
caring which vendor's model their harness was written against.

Status: **0.1.2** — wiki, modes, router and write gate are in service; this
release adds the CLI, the panel, the embedded manual and the packaging. Every
number below was measured on the reference machine and is reproducible with the
command next to it.

Distribution name: `uhb-harness` on PyPI (`uhb` is taken by an unrelated
project); the command is `uhb`.

---

## Why this exists

### 1. Routing that costs nothing

The intent router classifies a request and plans a model chain in **0.013 ms**
(p50) — no model call, no tokens. It is deterministic: same request, same policy,
same chain, every time. You can inspect the decision before spending anything.

| Measurement | Value | How |
| --- | --- | --- |
| `route()` | p50 0.013 ms · p95 0.020 ms · p99 0.026 ms | 2000 calls, `perf_counter` |
| `classify()` | p50 0.007 ms | 2000 calls |
| routing policy load | 0.50 ms cold · 0.22 ms cached | 11 models, 3 tiers |

### 2. Fail-closed permissions, not suggestions

Four execution modes, each a JSON file: `chat`, `dev`, `observe`, `review`. A
mode's write scope is a list of globs; **`deny` wins over everything**, an unknown
key in a profile is a load error, and a write path with no mode raises
`ModeRequired` instead of assuming one.

```bash
uhb modes show review     # write: [] — the agent physically cannot edit your files
```

A refused compile leaves every byte of the tree exactly as it was: all planned
writes are checked first, then written. Measured in the test suite by comparing a
byte-level snapshot of the whole tree before and after a refused write.

### 3. No vendor lock-in

| What | Where it lives |
| --- | --- |
| How to reach a provider | `uhb/config.yaml` (protocol, base URL, key **variable name**) |
| When to pick a model | `router/catalog.json` (tier, supported complexity) |
| What a turn may do | `hub/modes/profiles/*.json` |

Adding a vendor means one entry in the provider table. Adding a model to the policy
means one line in the catalog. A catalog entry no provider can serve is refused at
load time — a route pointing nowhere is worse than no route.

### 4. The free models and their quotas

Six `free` candidates ship in the catalog, reached through two OpenAI-compatible
providers. No key is read anywhere in UHB: the router carries the **variable
name**, and the caller resolves the value at call time.

| Model | Provider | Variable | Context | Declared quota |
| --- | --- | --- | --- | --- |
| `meituan/longcat-2.5:free` | `nous` | `NOUS_API_KEY` | 1 048 576 | 20 RPM · 50 RPD |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | `openrouter` | `OPENROUTER_API_KEY` | 1 000 000 | 20 RPM · 50 RPD |
| `recursive/flint-v1.0:free` | `openrouter` | `OPENROUTER_API_KEY` | 262 144 | 20 RPM · 50 RPD |
| `upstage/solar-mini4:free` | `nous` | `NOUS_API_KEY` | 524 288 | 20 RPM · 50 RPD |
| `google/gemma-4-31b-it:free` | `openrouter` | `OPENROUTER_API_KEY` | 262 144 | 20 RPM · 50 RPD |
| `stepfun/step-3.7-flash:free` | `nous` | `NOUS_API_KEY` | 262 144 | 20 RPM · 50 RPD |

The quotas are **policy ceilings set by the operator, not measurements**: the real
window is advertised by the provider in its `x-ratelimit-*` response headers. A
`429` is treated as an ordinary failure — recorded by exception class, never by
payload — and the chain moves on to the next candidate.

`free_first: true` on a profile (shipped on `chat`, `dev` and `review`) starts the
chain at the `free` tier before the mode's own tier. `paid` stays governed by
`allow_paid`: `free_first` reorders the chain, it never opens the paid tier.

The provider table's `routing` block carries the same policy for the contract-level
router: `fallback` names what answers when the chosen model cannot (one reference or
a list, tried in order), and `allow_paid: false` refuses a billed model — a `:free`
suffix, a loopback URL or a `local: true` provider are free — and routes to
`fallback` instead. With nothing free left to try, `route_model` raises
`PaidModelRefused` rather than spending quietly.

---

## The CLI

```bash
uhb status                      # version, git state, providers, policy, modes
uhb status --json               # same, machine-readable
uhb modes list                  # name, kind, tier, write scope, turn budget
uhb modes show dev              # the whole profile
uhb config list                 # every effective key (defaults included)
uhb config get routing.default
uhb config set routing.default deepseek/deepseek-flash
uhb run --mode dev --port 8080  # HTTP entrypoint (OpenAI + Anthropic shapes)
uhb execute --mode dev "..."    # one turn through the configured harness
uhb update --dry-run            # fetch and report; nothing moves
uhb update                      # git pull --ff-only, then prove it
uhb docs                        # path and URL of the embedded manual
uhb brain status                # RAG (8200), SiYuan (6806), MARI (55553)
uhb brain search "Bridge MARI"  # ask your own notes, through the local RAG
uhb tools exec --mode dev -f chart.py  # run a snippet; report the images it wrote
```

The `brain` group reads the local second brain when it exists — RAG on 8200, SiYuan
on 6806, the MARI bridge on 55553 — with 2 s probes and a 30 s search budget. A
stopped service is reported as `○`, not as an exception; a failed search raises,
because an empty list would read as "your notes have nothing".

`uhb run` also serves a small control panel, because a backend nobody can see is
a backend nobody trusts:

| Endpoint | What it answers |
| --- | --- |
| `GET /` | panel: a discreet **9+ UHB** badge (green/orange dot), a modal with the six commands that matter, and the manual download |
| `GET /status` | `connected`, `version`, `mode`, `harness`, providers with `key_loaded`, modes, docs URLs |
| `GET /docs/manuel.pdf` · `/docs/manual.pdf` | the manual (French · English), `application/pdf`, as an attachment |
| `POST /tools/execute` | runs a Python snippet as the server's own mode: streams, exit code, and the images it produced (path, MIME, optional `data:` URL) |
| `POST /shutdown` | stops the stack — compose project, local listeners on the stack ports, then itself — with a token and `{"confirm": true}`; `dry_run` returns the plan |

The panel is one self-contained HTML page: no CDN, no font download, no build step.
Embed the whole page in a dashboard with an iframe if you want the status view.
For the badge *and* its modal, copy the markup instead: the modal is a fixed
overlay sized to its viewport, so an iframe the size of the badge clips it.

### Stopping the stack from the interface

The panel also carries a **power button**, just above the badge. It opens a
confirmation, then `POST /shutdown` stops what the stack started:

| Target | What it does |
| --- | --- |
| `docker` | `docker compose down --timeout 10` for this project only — never `--volumes`, never `--rmi` |
| `local` | every listener on 8101 / 8200 / 6806 recognised as ours (`python`, `uvicorn`, `serveur_rag`, `SiYuan-Kernel`): a polite stop, then a forced kill after **5 s**. Anything else on those ports is reported and left alone |
| `self` | the server that answered, stopped **after** the `202` is flushed, so the interface reads "stopping" and never a connection reset |

Three gates, none of them optional: a **token** (`X-UHB-Token`, from
`$UHB_SHUTDOWN_TOKEN` or `~/.uhb/shutdown-token`), **`{"confirm": true}`** —
exactly true, not truthy — and a **closed list of targets**. `dry_run` returns the
plan and touches nothing:

```bash
curl -s -X POST http://127.0.0.1:8101/shutdown \
  -H "content-type: application/json" \
  -H "X-UHB-Token: $(cat ~/.uhb/shutdown-token)" \
  -d '{"confirm": true, "dry_run": true}'
```

That answer names the containers, the PIDs holding the ports, and the listeners it
refuses (`chrome.exe` on 8200 is reported, never killed). Remove `dry_run` and the
same request stops them in that order, reporting each step — including the ones
that failed: a process that survives the kill is printed as `STILL ALIVE`, never
counted as a success.

`uhb ui --markup` does **not** carry the kill switch: it appears only with
`--shutdown`, and even then the markup holds no token — set `data-uhb-token`
yourself or the button answers `401`. A widget pasted into someone else's
dashboard is not a kill switch by accident.

`uhb status` never prints a credential: a provider's key is reported as `loaded` or
`MISSING`, next to the variable **name**. `uhb config set` refuses any key whose
last segment looks like a secret — a token value never enters a file.

### The code execution tool

Asked for a chart, a model writes matplotlib code and stops there. `execute_code`
is the missing step, and it belongs in the backend, not in a prompt:

```bash
uhb tools exec --mode dev -f chart.py --base64 --json
```

```json
{"ok": true, "exit_code": 0, "stdout": "saved sine.png\n", "duration_s": 1.19,
 "images": [{"path": "~/.uhb/runs/run-20261116-000930-412f11/sine.png",
             "relative": "sine.png", "mime": "image/png", "bytes": 39464,
             "data_url": "data:image/png;base64,iVBORw0…"}]}
```

Three rules hold by construction, and each one is a test:

- **the mode decides.** `execute_code` must be in the mode's `tools` list: `chat`,
  `review` and `observe` refuse before a byte is written. `POST /tools/execute`
  also refuses a body that names another mode than the one the server was started
  with, so `--mode chat` cannot be escalated by a request;
- **the child is scrubbed.** Allow-listed environment, the run directory as `HOME`,
  `MPLBACKEND=Agg` so `savefig` works with no display. Every provider key in your
  shell is dropped, so a snippet cannot print one into a transcript;
- **nothing is silent.** A truncated stream says how many characters were dropped,
  an image above 1.5 MB is described by its path instead of inlined, and the timeout
  kills the process tree rather than leaving a child behind.

It is **containment, not a security boundary**: the snippet runs as your user, with
your filesystem. That is the point — plot my data, check this regex — and it is why
the HTTP route stays on loopback. See `uhb/tools/README.md` for the full contract.

### The tool catalogue

Every tool UHB can run is described once, in `uhb/tools/catalog.py`, built from the
constants the tool itself enforces (`MAX_TIMEOUT`, `MAX_CODE_CHARS`), so a limit
cannot be described here and applied elsewhere. Who may call it is still the mode's
`tools` list — the catalogue never grants anything:

```bash
curl -s 127.0.0.1:8101/tools?mode=chat
```

```json
{"tools": [{"name": "execute_code",
            "description": "Execute Python code in the authorized workspace…",
            "parameters": {"type": "object", "required": ["code"], "…": "…"},
            "category": "code", "execution_type": "subprocess", "version": "1",
            "enabled": true, "required_permissions": [], "compatible_modes": ["dev"],
            "available": false,
            "reason": {"code": "not_in_mode", "mode": "chat", "available_in": ["dev"],
                       "hint": "the current mode does not declare this tool; …"}}],
 "count": 1, "mode": "chat", "catalog_version": "1", "request_id": "…"}
```

`GET /tools/<name>` details one. `?mode=` answers a what-if question and changes
nothing: `POST /tools/execute` re-checks the mode the server was **started** with,
so reading the menu never opens a door. `available` is information; `reason` is a
code (`not_in_mode`, `disabled`) plus a hint, so a client translates instead of
parsing prose. In the chat, the model reaches the same payload through the
`describe_tools` tool of UH's `uhb` toolset.

`uhb update` has no `--force` and no `reset`. It fetches, refuses a dirty tree,
fast-forwards only, and reports the commit before and after. If the branch has
diverged it stops and says so.

---

## Full stack, or just the brain

UHB works alone: modes, router, write gate, wiki, panel. The second brain (a local RAG,
a SiYuan workspace, the MARI bridge) is **optional**, and the repository ships the code
and the recipe rather than the binaries:

```bash
docker compose up -d              # uhb + rag + siyuan, every port bound to 127.0.0.1
docker compose up -d uhb siyuan   # skip the RAG: no index, no torch download
```

Without Docker:

```bash
./install_services.sh             # or install_services.ps1 on Windows
                                  # downloads SiYuan, verifies the published sha256
python -m uhb.services.install --with rag --yes    # ~1.5 GB (torch, faiss, flashrank)
python -m uhb.services.install --with mari --mari-src <path to the .NET project>
uhb brain start --dry-run         # what would be launched, and from where
uhb brain start                   # launches what is installed, prints pids and urls
```

Nothing downloaded lands in git: `uhb/bin/` is ignored and never packaged. One honest
limit remains — **the RAG index is yours to build**: it is made of your notes, a fresh
clone has an empty brain, and `/sante` says `fragments: 0` rather than inventing results.

### Repository layout

The monorepo carries our own code; heavy or third-party trees are fetched, not copied:

```
uhb/services/rag/              our RAG service (FastAPI + faiss), ~50 KB of code
uhb/services/mari/bridge/      our MARI bridge (5 C# files, 18 KB)
uhb/services/mari/framework/   git submodule -> Antoine-Thomas/mari (MIT, fork of kaonomi-net/mari)
uhb/bin/                       DOWNLOADED at install time, never committed, never packaged
docker/                        Dockerfile.uhb, Dockerfile.rag
docker-compose.yml             uhb + rag + siyuan, every port on 127.0.0.1
install_services.sh|.ps1       thin wrappers -> python -m uhb.services.install
```

Clone with the submodule, or the MARI build will refuse with a message saying so:

```bash
# one shot (Linux, macOS, and Windows from cmd or PowerShell)
git clone --recurse-submodules https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend.git

# Windows + Git Bash: the one-shot form can fail with "not a git repository: /tmp/...",
# a path quirk of the submodule helper, not of this repository. Two steps work:
git clone https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend.git
cd UHB-Universal-Harness-Backend && git submodule update --init
```

`install.sh --full` chains everything: the package, SiYuan (sha256-verified download),
the RAG dependencies, and the MARI bridge built from `uhb/services/mari/bridge`.

## Install

Requires Python 3.11+. Nothing else (httpx is the only runtime dependency).

UHB runs on the three major operating systems: Windows 10 and 11 (cmd,
PowerShell, Windows Terminal), macOS (Intel and Apple Silicon), and Linux
and Unix (all major distributions). CI runs the full test suite on
`ubuntu-latest`, `macos-latest` and `windows-latest` with Python 3.11, 3.12
and 3.13 (see `.github/workflows/ci.yml`).

```bash
pip install uhb-harness          # the CLI + the panel + the embedded manual
pip install "uhb-harness[yaml]"  # additionally enables `uhb config set`
```

From a checkout:

```bash
git clone https://github.com/Antoine-Thomas/UHB-Universal-Harness-Backend.git
cd UHB-Universal-Harness-Backend
./install.sh              # creates .venv, installs, verifies `uhb --version`
./install.sh --with-yaml  # additionally enables `uhb config set`
```

Or, by hand:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[yaml]"
uhb --version
```

The installer never uses `sudo`, never writes outside the checkout, and is a no-op
reinstall on a second run. `./install.sh --dry-run` prints the commands and changes
nothing.

---

## Architecture

```
control plane (Harness Maxxing · OpenClaw · Hermes · your CLI)
        │
        ▼
┌─────────────── UHB ────────────────────────────────────────────┐
│  modes      what a turn may read, write, and which tools exist  │
│  router     classify → plan a tiered chain → execute w/ fallback│
│  guard      check every planned write, then write               │
│  wiki       raw sources → compiled pages → schema + lint        │
│  entrypoints  cli · HTTP (OpenAI + Anthropic) · fake `claude`   │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
   providers: DeepSeek · Kimi · OpenAI · Anthropic · local Ollama
```

Fallback rules, in code and in the report: a remote provider whose key variable is
absent is **skipped, not called**; a provider that raises is recorded and skipped
(the exception *class* is kept, never a payload); if every candidate fails the call
raises with its attempts — UHB never returns an empty string that reads like an
answer.

Same lever applied to thinking models: a routed call sends
`reasoning_effort: none` by default, because extraction is not deliberation.
Measured on `qwen3:8b`: **0.7 s instead of 7.1 s** for a one-word reply.

---

## Verified end to end

Run against the real thing on the reference machine, not against a mock:

| What | Evidence |
| --- | --- |
| `uhb run` + a real harness | `uhb run --mode chat --port 8099`, then `POST /v1/chat/completions` → `{"model": "deepseek/deepseek-flash", "content": "salut"}`, ~5.6 s round trip through the `hermes` subprocess |
| `uhb execute` | `uhb execute "Réponds uniquement: bonjour" --mode chat` → `bonjour`, exit 0, 5.6 s |
| the routed model reaches the harness | the child's argv is asserted to contain `--provider deepseek -m deepseek-flash` |
| a compile with a paid model | `deepseek-flash` on a three-sentence note: **4 pages, 8 links, `lint ok = True`**, one call, 4.44 s |
| the same compile, free model | `ollama/qwen3:8b`: 1 page, 1 orphan, 2 dead links, 7.75 s — the routing works, the quality does not |
| a vendor failure | every candidate answering 500 → `AllProvidersFailed`, three recorded attempts, nothing written |

Two of those runs also corrected the product: the DeepSeek model ids in
`config.example.yaml` (`deepseek-v4.1-*`) **do not exist** — the vendor serves
`deepseek-flash` and `deepseek-v4-pro`, discovered by calling `/v1/models` — and
the adapter kept a stray `\r` on every token read from a Windows subprocess.

## Adjacent layer: the Council and its Oracle

Some harnesses run an adversarial deliberation layer above the model — Hermes Agent can bind
`hermes-council` (MIT, `Ridwannurudeen/hermes-council`), an MCP server with five personas:
advocate, skeptic, **oracle**, contrarian, arbiter. The Oracle is the empirical voice: it argues
from base rates and statistics rather than from principle.

It is worth being exact about the relationship, because "compatible" would be the easy word and
the wrong one:

| | The Council | UHB |
| --- | --- | --- |
| Speaks | MCP (stdio), bound to a harness profile | HTTP + CLI, bound to a harness |
| Judges | whether an irreversible **action** should happen | whether a **write** may happen, and which model answers |
| Latency | ~109 s measured for one gate (3 personas, free model) | 0.013 ms to decide, then the model's own time |
| Verdict | consultative | fail-closed, enforced before the first byte is written |

**UHB has no MCP client, and this is not an integration.** The Council lives in the harness; UHB
lives underneath it. Pointed at the same harness, they cover different questions — the Council
asking "should this be done at all", UHB answering "this mode may not write there, and this model
is the one that answers". Nothing in this repository talks to `hermes-council`, and the ~109 s
gate belongs nowhere near an interactive path.

## Limits, stated plainly

- **The compile was measured once.** `COMPILE_COMPLEXITY = "standard"` produced four
  clean pages for a three-sentence note; a 50-source ingest is untested, and that
  constant is the first knob to re-examine.
- **Only DeepSeek is verified against its vendor.** Kimi, OpenAI and Anthropic
  entries have never been resolved against a real catalog — treat them as declared,
  not proven.
- **`uhb update` does not restart anything.** UHB ships no supervisor; systemd, Task
  Scheduler or your terminal owns the process. Killing a PID read from a file is a
  worse default than saying "restart it yourself".
- **The Harness Maxxing and OpenClaw adapters are skeletons.** They declare the
  contract; they have not been run against those products yet.
- **A harness running as a subprocess is outside the gate.** UHB gates its own
  writes (`wiki`, `memory`); what `hermes` does on its own filesystem is not
  something a library can police.

---

## Roadmap

- ☑ 1.1 wiki: three layers (`raw/`, `wiki/`, schema), `ingest` / `query` / `lint`
- ☑ 1.2 modes: four profiles, fail-closed permissions, prompt envelope
- ☑ 1.3 router: classification, tiered chain, fallback, measured latency
- ☑ 1.4 wiring: gate on every write path, router in the pipeline and entrypoints
- ☑ 2 packaging: CLI, installer, this README
- ☑ 2.1 free tier: six free candidates, `free_first`, and the `allow_paid` cost policy
- ☑ 3.1 licence: MIT for the contract, FSL-1.1-MIT for the implementation — the
  split is stated in the licence section below, including what a non-OSI licence
  means for adopters
- ☑ 3.2 proof: `docker compose` end to end (`docs/proof/docker-compose-e2e.md`)
  and the `9+ UHB` widget in a real page (`docs/proof/widget-proof.md`)
- ☐ 3.3 adapters: `harness_maxxing` and `openclaw` run against the real products.
  Both are written against their documented interfaces — a launcher binary for
  `MAXXING_CLAUDE_BIN`, a `CliBackendConfig` for `api.registerCliBackend` — and
  neither has been driven by a live control plane yet
- ☐ 3.4 release: 1.0.0 — `CHANGELOG.md` entry, version bump, tag, GitHub release,
  PyPI upload (`docs/RELEASING.md` is the checklist)

## Tests

`pytest uhb/tests -q` — no network: the model is injected, git is injected, and
the skips depend on the machine, not on the code — one end-to-end test is opt-in
against a local Ollama (`UHB_OLLAMA_E2E=1`), the other skips while the MARI
framework submodule is not initialised (`git submodule update --init`). All tests
pass on every supported platform; the CI matrix on `ubuntu-latest`,
`macos-latest` and `windows-latest` (Python 3.11-3.13) is the source of truth
(see `.github/workflows/ci.yml`).

Four security greps run before every commit, on every touched file: no key prefix,
no credential value, no absolute path, no environment dump. Current result: 0.

## Development conventions

Three rules the CI enforces, so contributors do not have to guess:

- **No test counts in prose.** The CI matrix is the source of truth for
  pass/skip figures; a hardcoded number goes stale on the first PR.
- **Multi-OS CI on every PR.** The suite runs on `ubuntu-latest`,
  `macos-latest` and `windows-latest` with Python 3.11-3.13 (see
  `.github/workflows/ci.yml`). Merge only after 9/9 green, macOS included.
- **Resolve Windows shims before spawning.** Before
  `asyncio.create_subprocess_exec`, resolve `argv[0]` with `shutil.which()`:
  a pip-installed `.cmd` shim is invisible otherwise. Keep `shell=True` off.
  Motif landed in commit `b1b2465`.

## Licence

Two licences, one per layer — the split is deliberate, and the boundary is the
path:

| Path | Licence | What it means |
| --- | --- | --- |
| `SPEC.md`, `ARCHITECTURE.md`, `ADAPTERS.md`, `NON-GOALS.md`, `README.md` | **MIT** (`LICENSE`) | implement the contract freely, in any product, commercial or not |
| `uhb/` — the implementation | **Fair Source, FSL-1.1-MIT** (`LICENSE-IMPLEMENTATION.md`) | read it, run it, modify it, use it commercially; you may not resell it as a competing hosted service. Each release becomes MIT two years after publication |

The contract is open so harnesses can adopt it without a legal conversation. The
implementation is Fair Source so the work can be funded. Both texts ship inside
the package: no one has to guess which one covers what.

**FSL-1.1-MIT is source-available, not open source.** It is not an OSI-approved
licence, and no wording here should be read as claiming it is. Concretely:

- you may read, run, modify and use the implementation commercially, including in
  a paid product;
- what it forbids is offering it (or a derivative) as a competing hosted or
  distributed service;
- each released version converts to **MIT two years after its release date**,
  automatically and version by version — not the project as a whole, and with
  nothing to request;
- `pyproject.toml` declares the SPDX identifier `FSL-1.1-MIT`, and package
  indexes will list it as a non-OSI licence. That is the intended reading.

The specification layer above (`SPEC.md`, `ARCHITECTURE.md`, `ADAPTERS.md`,
`NON-GOALS.md`, `README.md`) is MIT and OSI-approved: the contract can be
implemented in any product, with no time limit and no condition.

Built by Thomas Leroyer (Searching Murphy).
