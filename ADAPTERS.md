# ADAPTERS — contract per control plane

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

<!-- Budget: 270 lines, inside the revised 200-300 range (G.2). The original
     150-200 target predates the DeepSeek Harness section, which accounts for
     39 of these lines. No measured finding was removed to fit the old number. -->

One note per known control plane: what the product really offers, through which
door an external backend enters, what the adapter must supply, the effort and the
risk.

Each note cites the evidence measured in Phase 0 (files and docs read in the public
repositories); a claim without evidence is marked **[TO VERIFY]**.

---

## 1. Harness Maxxing

**Repository** — https://github.com/makushingu/harness-maxxing (Fair Source,
source-available, **not** OSI).

### Contract provided

**No native execution contract.** The documented HTTP API (`docs/dev/API.md`,
v2.0) is a *control plane* API: `/api/sessions`, `/api/sessions/{id}/stream`,
`/api/crons`, `/api/skills`, `/api/claude/settings`. No endpoint declares an
execution backend, and the product requires a Claude subscription or an Anthropic
key.

### Integration path

**a) Fake CLI — the solid door.** `server/maxxing/sessions/spawn.py`:

```python
def claude_bin() -> str:
    explicit = os.environ.get("MAXXING_CLAUDE_BIN")
    if explicit:
        return explicit
    return shutil.which("claude", path=base_env()["PATH"]) or "claude"
```

`MAXXING_CLAUDE_BIN` is documented (`docs/dev/INTERNALS.md`, `CONTRIBUTING.md`) and
used by the product's own test suite (`server/tests/fake_claude.py`, 541 lines).
Substitution contract:

- **argv**: `-p --output-format stream-json --verbose`, then `--model <id>`,
  `--permission-mode <mode>`, `--mcp-config <file> --permission-prompt-tool <tool>`,
  and `--session-id <csid>` (fresh thread) or `--resume <csid>`.
- **input**: `--input-format stream-json`; the prompt arrives on stdin as a JSONL
  user message, stdin left open (this is what allows a *steer* mid-turn).
- **output**: JSONL on stdout, `type` ∈ `assistant`, `user`, `result`, `system`
  (`subtype: init|task_started|…`), `stream_event`, `control_response`.
- **control**: `{"type":"control_request","request":{"subtype":"interrupt"}}` on
  stdin interrupts the turn.
- **filtering**: lines with a non-empty `parent_tool_use_id` (sub-agents) are
  dropped from normalisation (`server/maxxing/sessions/normalize.py`).
- **sessions**: Claude Code `stream-json`, re-read by the internal SSE
  `GET /api/sessions/{id}/stream?from=` (`event: events|state|reset`).

**b) Anthropic proxy.** The product launches the official CLI, which honours
`ANTHROPIC_BASE_URL`. `base_env()` copies `os.environ`, so a variable set in the
server's systemd unit reaches the `claude` process. Caveat: that variable sits in
`_RESERVED` — a vault secret flagged `expose` cannot set it, and the user docs do
not present this path as a hook. **[hypothesis]** it works, but it is not a
contract.

### Decision: fake CLI (`MAXXING_CLAUDE_BIN`)

We chose the fake CLI path over the Anthropic proxy because:

1. It is the mechanism sanctioned by Harness Maxxing's own test suite
   (`server/tests/fake_claude.py`, 541 lines).
2. `ANTHROPIC_BASE_URL` is in Harness Maxxing's `_RESERVED` list (a secret vault
   cannot set it), which makes the proxy path non-contractual and fragile.
3. The fake CLI contract is fully documented: argv, stdin `stream-json`, stdout
   JSONL types `assistant`/`user`/`result`/`system`/`control_response`,
   interruption through `control_request` subtype `interrupt`.

The UHB adapter is an independent reimplementation. Harness Maxxing is Fair Source,
not MIT; copying `fake_claude.py` verbatim would violate the license.

### What the adapter must supply

A binary that speaks the contract above (`execute_task` ≈ one invocation per turn,
`cancel` ≈ `control_request interrupt`, `health` ≈ immediate exit with no model
call), or an Anthropic-compatible endpoint on `127.0.0.1:PORT` with
`ANTHROPIC_BASE_URL` set in the service environment.

### Effort — 1 to 3 weeks

The fake CLI is the short path: the format is already Claude Code's, so an adapter
that emits valid `stream-json` works within days; the rest is session handling, MCP
and tool streaming.

### Risk — high

Non-contractual interface: the flags are built inside `runner.py`, not in an
extension doc, and change across versions (per-build release notes,
`docs/upgrade/2.0.0a1xx.md`). Fair Source license: redistributing a modified fork is
allowed, using it inside a paid offer is not, without agreement. An adapter wired
through an environment variable copies none of the product's code.

**Verdict — can an external backend plug in without patching the code?**
**Partially.** Yes through `MAXXING_CLAUDE_BIN` or `ANTHROPIC_BASE_URL`, no through
a documented execution contract.

---

## 2. OpenClaw

**Repository** — https://github.com/openclaw/openclaw (MIT, OpenClaw Foundation).

### Contract provided

Two documented and distinct execution surfaces.

**a) Agent harness plugin** — `docs/plugins/sdk-agent-harness*` (9 pages). Import
`openclaw/plugin-sdk/agent-harness`. Required shape:

```ts
const harness: AgentHarnessV2 = {
  id: "my-harness",
  label: "My native agent harness",
  supports(ctx) { /* { supported, priority } or { supported:false, reason } */ },
  async runAttempt(params) { /* runs the prepared turn */ },
};
api.registerAgentHarness(harness);
```

- **Required**: `id`, `label`, `supports`, `runAttempt`.
- **Optional**: `runIsolatedCompletionV2`, `delegatedExecutionPluginIds`,
  `authBootstrap: "harness"`, `workspaceEnvironment`.
- **Core prepares**: provider and model, auth, context budget, transcript,
  workspace, tool policy, callbacks. The harness receives `params.prompt`,
  `params.tools`, `params.images`, `params.onPartialReply`, `params.onAgentEvent`.
- **Real examples**: `extensions/muse/index.ts`, `extensions/copilot/index.ts`,
  `extensions/acpx/index.ts`, `extensions/codex/index.ts` — all call
  `api.registerAgentHarness(...)`.

**b) CLI backend plugin** — `docs/plugins/cli-backend-plugins.md` and
`docs/gateway/cli-backends.md`: `api.registerCliBackend({ id, modelProvider,
liveTest, config })`, where `config` carries `command`, `args`, `resumeArgs`
(`{prompt}`, `{sessionId}`), `output`/`resumeOutput` (`json`, `jsonl`, `text`),
`jsonlDialect` (`claude-stream-json`, `gemini-stream-json`), `input`
(`arg`/`stdin`), `sessionArgs`, `sessionMode`, `modelArg`, `modelAliases`,
`env`/`clearEnv`, `imageArg`, `bundleMcp`/`bundleMcpMode`, `nativeToolMode`,
`liveSession`.

### Is text-only the only mode?

**No.** The third-party page `docs.openclaw.kr/gateway/cli-backends` describes CLI
backends as a net: "text in / text out, tools disabled, no streaming, fallback
only". The upstream repository documentation, which is current, says the opposite
on all three points:

- "A CLI backend can be an agent's **primary runtime** or a fallback."
- "**JSONL streaming** for CLIs that support it."
- tools available through `bundleMcp: true` and the loopback MCP bridge (tool calls
  are not injected into the CLI protocol; the CLI sees the Gateway tools over MCP).

The Korean mirror is **stale**: useful for describing the conservative default
behaviour, not the state of the art.

### Decision: CLI Backend for v0.1, AgentHarnessV2 for v0.2

The CLI Backend is not a downgrade: it covers 5 of the 8 pivot functions
(`execute_task`, `stream`, `cancel`, `health`, `resolve_provider`). The
AgentHarnessV2 adds the 3 remaining functions (`sync_memory`, `load_skill`,
`route_model`).

Two levels of capability, not two versions of stability. The 4 existing
implementations (`extensions/muse`, `copilot`, `acpx`, `codex`) confirm that CLI
Backend is usable as a primary runtime, not a text-only fallback.

### What the adapter must supply

A plugin that declares `cliBackends` in `openclaw.plugin.json`, calls
`api.registerCliBackend(...)` (short path: `command` = the UHB binary, `output:
"jsonl"`, `jsonlDialect: "claude-stream-json"`, `sessionArgs` + `sessionMode:
"existing"`), and for the native path an `AgentHarnessV2` whose `runAttempt`
implements `execute_task`.

### Effort — 1 to 2 weeks

Documented, versioned contract, illustrated by four implementations in the
repository; the real work is the event-flow translation and the tool policy.

### Risk — medium

`AgentHarnessV2` is explicitly **experimental**: "the contract is still experimental
because the parameter types intentionally mirror the current embedded runner", and
so is third-party harness installation. The CLI backend path is more stable
(declarative config) but less capable.

**Verdict — primary path possible, not just fallback?** **Yes.** Documented for CLI
backends; experimental but supported for agent harnesses.

---

## 3. DeepSeek Harness (DSH)

**Nature — an execution runtime, not a control plane.** DSH owns the agent loop,
the sandbox, the tools and the session log; it owns no product UI, no crons and no
notifications. It is the only product in this list that is already model-agnostic
**and** plugin-based: "everything is a plugin" through Cordis, with DeepSeek,
OpenAI, Anthropic and custom endpoints supported natively. It therefore does not
need to be made universal — it needs a contract to be usable from a control plane.

### Contract provided

Native Cordis plugin. Append-only session log, replayable and forkable. Replaceable
agent loop through a `pre-step` / `request` / `request-error` waterfall. Sandbox,
tools and plugins are all Cordis components. Status: **developer preview**, breaking
changes announced.

### Integration path

**Primary path** — DSH as the runtime behind a UHB-native control plane: the control
plane speaks the 8 functions, UHB routes to DSH, DSH executes (sandbox, tools,
session log) and calls the model through its own model adapter. No model protocol is
translated on this path: UHB carries tasks and events.

**Secondary path** — a DSH plugin exposing the 8 functions to existing control
planes (Harness Maxxing, OpenClaw): `execute_task` over the agent loop, `cancel` over
the waterfall, `stream` over the append-only session log (replay and fork become
backend capabilities), `health` without inference.

### Effort and risk

Effort: 2 to 4 weeks for the secondary path; the primary path depends on a
UHB-native control plane that does not exist yet, so it cannot be estimated in
isolation. Risk medium to high: developer preview, breaking changes announced, the
contract can move without notice. DSH is therefore marked **`supported,
experimental`** (SPEC.md §6), and a DSH adapter is tested against a pinned version,
never against `main`.

---

## 4. Hermes

**Contract provided** — no external execution contract documented to date. Hermes is
a full agent runtime (gateway, profiles, skills, memory, plugins), not a control
plane in this document's sense. **[TO VERIFY]**: whether the gateway exposes a
harness/backend registration surface.

**Integration path** — extraction of the eight functions from the existing pieces:
provider configuration (routing and key resolution), skill library (loading),
per-profile memory files (synchronisation), gateway execution loop (task, stream,
cancel). The adapter then exposes those functions as a UHB backend.

**What the adapter must supply** — a Python module implementing the eight functions,
`stream()` wired to the existing event channel, `health()` without inference.

**Effort — 2 to 3 weeks. Risk — high.** The coupling between gateway, profiles and
tools is strong: extracting the eight functions without duplicating session logic is
the real work. An adapter that reimplements routing will diverge at the product's
first change.

---

## 5. Synthesis

| Product | Nature | Execution contract | Primary possible | Risk | Effort |
|---|---|---|---|---|---|
| Harness Maxxing | control plane | none (fake CLI + proxy) | yes, non-contractual | high | 1–3 wks |
| OpenClaw | control plane | `registerCliBackend` + `registerAgentHarness` | yes | medium | 1–2 wks |
| DeepSeek Harness | runtime | Cordis plugin: the 8 functions must be exposed | yes, as a backend | medium to high | 2–4 wks |
| Hermes | runtime | none | yes, by extraction | high | 2–3 wks |

The only contract **documented as a contract** is OpenClaw's. The other two doors
exist and work: that is precisely why UHB has value — naming and stabilising what
each product leaves open.
