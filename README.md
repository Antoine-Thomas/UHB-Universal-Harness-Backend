# UHB — Universal Harness Backend

> **Your harness gives you the body — the UI, the sessions, the automations.
> UHB gives it the brain — the memory, the routing, the absolute permissions.**

UHB is not another agent. It is the execution backend underneath one: modes that
decide what a turn may do, a router that decides which model answers, a write gate
that refuses before it touches a file, and a compiled wiki that remembers.

Harness Maxxing, OpenClaw, Hermes Agent or your own CLI plug into it — and stop
caring which vendor's model their harness was written against.

Status: **0.1.0** — wiki, modes, router and write gate are in service; this
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
| routing policy load | 0.50 ms cold · 0.22 ms cached | 5 models, 3 tiers |

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

The panel is one self-contained HTML page: no CDN, no font download, no build step.
Embed the whole page in a dashboard with an iframe if you want the status view.
For the badge *and* its modal, copy the markup instead: the modal is a fixed
overlay sized to its viewport, so an iframe the size of the badge clips it.

`uhb status` never prints a credential: a provider's key is reported as `loaded` or
`MISSING`, next to the variable **name**. `uhb config set` refuses any key whose
last segment looks like a secret — a token value never enters a file.

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
- ☐ 3 distribution: public release of the HUB (licence decision: MIT spec + Fair
  Source implementation), then the adapters against the real products

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

Built by Thomas Leroyer (Searching Murphy).
