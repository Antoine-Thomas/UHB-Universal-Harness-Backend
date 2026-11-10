# SPEC — UHB v0.1

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

<!-- Budget: 592 lines of content. The 500-line target was unattainable with 3
     examples at 30-50 lines each (C.4 constraint): 447 lines of spec + 90 lines
     of examples minimum = 537. Content was not cut to meet an arbitrary number. -->

Normative. **MUST**, **SHOULD** and **MAY** carry their RFC 2119 meaning.

---

## 1. Definitions

**Control Plane** — the layer that decides, governs and supervises. It owns the
user interface, session lifecycle, scheduled jobs (crons), product-level memory,
notifications and policies (permissions, quotas, approvals). It does not talk to
models: it talks to UHB. Measured examples: Harness Maxxing (sessions, crons,
Telegram), OpenClaw (Gateway, channels, Control UI), Hermes (gateway, profiles,
memory).

**Runtime / Data Plane** — the layer that executes. It owns the model loop: it
sends the prompt, receives text and tool calls, executes those tools, manages its
context and compaction, and returns events. It knows nothing of product sessions,
crons or UI.

**UHB** — the interface contract between the two. It defines eight functions,
three transport protocols and one state machine. It owns neither the decision
(that is the Control Plane) nor the execution (that is the Runtime): it translates,
routes and normalises.

An **adapter** exposes the contract inside the real interface of a given control
plane. A **backend** implements the contract on top of a given runtime. Both are
out of scope for this repository.

### Base types

```ts
type MemoryLevel = "workspace" | "project" | "topic";
type StopReason  = "done" | "failed" | "cancelled" | "timeout" | "budget";

interface ToolSpec { name: string; description: string; input_schema: Record<string, unknown>; }

interface TaskContext {
  session_id: string;        // stable identifier supplied by the control plane
  workspace: string;         // working root, absolute path
  topic?: string;            // logical sub-folder (project/topic)
  system?: string;           // system prompt already assembled by the control plane
  tools?: ToolSpec[];        // tools allowed for this turn (empty = none)
  model_ref?: string;        // imposed ref; otherwise route_model decides
  memory?: MemoryLevel;
  metadata?: Record<string, string>;
  timeout_ms?: number;
  idempotency_key?: string;  // see §5
}

type StreamEvent =
  | { type: "start";       task_id: string; model_ref: string; at: number }
  | { type: "text";        delta: string; at: number }
  | { type: "thinking";    delta: string; at: number }
  | { type: "tool_call";   id: string; name: string; input: unknown; at: number }
  | { type: "tool_result"; id: string; ok: boolean; output: string; at: number }
  | { type: "usage";       input_tokens: number; output_tokens: number; at: number }
  | { type: "error";       code: string; message: string; retryable: boolean; at: number }
  | { type: "end";         task_id: string; reason: StopReason; at: number };

interface ModelIntent { capability: "code" | "reasoning" | "vision" | "fast" | "long-context";
                        max_cost_per_mtok?: number; min_context?: number;
                        locality?: "local" | "remote" | "any"; preferred?: string[]; }

interface ModelRef { model_ref: string; rationale?: string; }

interface ProviderDescriptor { protocol: "anthropic" | "openai" | "deepseek";
                               base_url: string; api_key_env: string; model_id: string;
                               extras?: Record<string, string>; }

interface SkillDescriptor { skill_id: string; name: string; version: string;
                            description: string; path: string; requires?: string[]; }

interface MemorySyncResult { level: MemoryLevel; path: string; bytes: number; conflict: boolean; truncated: boolean; }
interface CancelResult     { cancelled: boolean; reason: string; already_done: boolean; }
interface HealthStatus     { ok: boolean; version: string; queue_depth: number; at: number;
                             providers: Array<{ model_ref: string; reachable: boolean; latency_ms?: number }>; }
```

---

## 2. The eight pivot functions

Every function MUST exist. A function that has no meaning for a given runtime
MUST return an explicit error (`code: "unsupported"`), never a silent success.

### 2.1 `execute_task`

```ts
execute_task(prompt: string, context: TaskContext): AsyncIterable<StreamEvent>
```

Runs one agent turn and yields its events as they happen. This is the central
function: without it, a backend is not a backend.

**Constraint** — the first event emitted MUST be `start`, the last MUST be `end`.
No event after `end`. If `context.idempotency_key` is supplied, a second call with
the same key MUST NOT create a second turn (§5). The prompt MUST NOT contain the
system prompt: that travels in `context.system`.

```ts
const it = uhb.execute_task("fix the failing test", {
  session_id: "s-7f3a", workspace: "/work/projects/acme",
  topic: "projects/acme/topics/api", system: "You are a coding agent.",
  tools: [{ name: "bash", description: "shell", input_schema: { type: "object" } }],
  model_ref: "deepseek/deepseek-v4.1-flash",
});
for await (const ev of it) render(ev);

// yielded stream
{ type: "start", task_id: "t-91", model_ref: "deepseek/deepseek-v4.1-flash", at: 1770000000.1 }
{ type: "thinking", delta: "Let me look at the test first…", at: 1770000000.4 }
{ type: "tool_call", id: "call_1", name: "bash", input: { cmd: "pytest -q" }, at: 1770000000.9 }
{ type: "tool_result", id: "call_1", ok: false, output: "1 failed", at: 1770000001.8 }
{ type: "text", delta: "The assertion is about…", at: 1770000002.1 }
{ type: "usage", input_tokens: 1842, output_tokens: 233, at: 1770000002.4 }
{ type: "end", task_id: "t-91", reason: "done", at: 1770000002.4 }
```

### 2.2 `sync_memory`

```ts
sync_memory(level: MemoryLevel, content: string): MemorySyncResult
```

Writes or merges a block of memory at the requested level, without ever
overwriting the content of another level.

**Constraint** — idempotent by content: rewriting the same `content` at the same
level MUST NOT produce a duplicate. A conflict (concurrent write) MUST be reported
through `conflict: true` rather than resolved silently. UHB negotiates the format
with the runtime; it does not impose it (see NON-GOALS §5).

```ts
uhb.sync_memory("project", "## Decisions\n- API in Rust, not Go.");
// → { level: "project", path: "/work/projects/acme/memory.md",
//     bytes: 46, conflict: false, truncated: false }
```

### 2.3 `load_skill`

```ts
load_skill(skill_id: string): SkillDescriptor
```

Makes a procedure available to the model: identifier, version, description,
readable path, dependencies.

**Constraint** — synchronous and with no network call: the model cannot wait for
a skill load. An unknown `skill_id` MUST raise a typed error, not return an empty
descriptor.

```ts
uhb.load_skill("code-quality:review");
// → { skill_id: "code-quality:review", name: "Code review", version: "1.2",
//     description: "Targeted review: correctness, security, style",
//     path: "/skills/code-quality/SKILL.md", requires: ["git"] }
```

### 2.4 `route_model`

```ts
route_model(intent: ModelIntent): ModelRef
```

Picks a model reference from an intent (capability, cost, context, locality,
preferences), not from a vendor name.

**Constraint** — deterministic for a given intent and configuration state, and MUST
document its `rationale`. It MAY consult `health()` internally; it MUST NOT perform
inference in order to decide.

```ts
uhb.route_model({ capability: "code", min_context: 200_000, locality: "remote" });
// → { model_ref: "deepseek/deepseek-v4.1-pro",
//     rationale: "code + 256K context, cost < $0.60/Mtok" }
```

### 2.5 `stream`

```ts
stream(output: StreamEvent): void
```

Pushes an event toward the consumption surface (live UI). Mandatory: this is what
makes tool calls, tokens and diffs visible in real time.

**Constraint** — synchronous, non-blocking, ordered. A backend MUST be able to
produce `stream` without knowing who consumes it (SSE, WebSocket, JSONL on stdout,
log file). The contract does not fix the transport: it fixes order and shape.

```ts
uhb.stream({ type: "text", delta: "```diff\n- assert x == 1\n+ assert x == 2\n```", at: 1770000003 });
// → no return value; the event is consumed by the channel opened in §2.1
```

### 2.6 `cancel`

```ts
cancel(task_id: string): CancelResult
```

Requests that a task stop. Cancellation MUST be cooperative: the backend lets the
runtime finish an in-flight tool execution, then emits `end` with
`reason: "cancelled"`.

**Constraint** — idempotent: cancelling an already finished task returns
`{ cancelled: false, already_done: true }` and succeeds. An unknown `task_id`
returns `already_done: true` with `reason: "unknown_task"`.

```ts
uhb.cancel("t-91");
// → { cancelled: true, reason: "user_stop", already_done: false }
```

### 2.7 `health`

```ts
health(): HealthStatus
```

Backend status: version, reachable providers, queue depth.

**Constraint** — MUST NOT spend tokens or start inference. MUST answer in under 2 s
even when a provider is unreachable (short per-provider timeout, partial result
rather than global failure).

```ts
uhb.health();
// → { ok: true, version: "uhb/0.1", queue_depth: 0, at: 1770000010,
//     providers: [{ model_ref: "deepseek/deepseek-v4.1-flash", reachable: true, latency_ms: 41 }] }
```

### 2.8 `resolve_provider`

```ts
resolve_provider(model_ref: string): ProviderDescriptor
```

Turns a model reference into call coordinates: protocol, base URL, the environment
variable that carries the key, the model's native identifier.

**Constraint** — `api_key_env` is a variable **name**, never a value: the contract
carries no secret. An unresolvable `model_ref` MUST raise a typed error listing the
known references.

```ts
uhb.resolve_provider("deepseek/deepseek-v4.1-flash");
// → { protocol: "deepseek", base_url: "https://api.deepseek.com/v1",
//     api_key_env: "DEEPSEEK_API_KEY", model_id: "deepseek-v4.1-flash",
//     extras: { reasoning: "on" } }
```

---

## 3. The three supported protocols

### 3.1 Anthropic — `POST /v1/messages`

For Claude Code and any runtime that imitates its dialect. This is the protocol
most Claude-first control planes already speak.

| Field | Status | Note |
|---|---|---|
| `model` | required | native model identifier |
| `max_tokens` | required | output ceiling |
| `messages[]` | required | `role: user\|assistant`, `content` text or blocks |
| `system` | optional | string or blocks |
| `tools[]` | optional | `{name, description, input_schema}` |
| `tool_choice` | optional | `auto\|any\|tool` |
| `stream` | optional | `true` → SSE `message_start`, `content_block_delta`, `message_stop` |
| `stop_sequences`, `metadata` | optional | |

Non-streamed response: `{ id, type: "message", role, content: Block[], stop_reason,
usage }` where `Block` = `{type:"text",text}` | `{type:"tool_use",id,name,input}` |
`{type:"thinking",thinking}`. In streaming mode each delta is a `content_block_delta`
carrying an `index` — the index must be preserved, otherwise tool diffs arrive out
of order.

### 3.2 OpenAI — `POST /v1/chat/completions`

For DeepSeek, Kimi, GLM, OpenAI, Ollama, vLLM and most providers. This is the
market's lowest common denominator.

| Field | Status | Note |
|---|---|---|
| `model` | required | native identifier |
| `messages[]` | required | `role: system\|user\|assistant\|tool` |
| `max_tokens` | optional | |
| `tools[]` | optional | `{type:"function", function:{name, description, parameters}}` |
| `tool_choice` | optional | `auto\|none\|required\|{...}` |
| `stream` | optional | `true` → SSE `data: {...}` + `data: [DONE]` |
| `stream_options` | optional | `{include_usage: true}` |
| `temperature`, `top_p` | optional | |

Response: `{ id, choices:[{ index, message:{role, content, tool_calls?},
finish_reason }], usage }`. `tool_calls[].function.arguments` is a **JSON string**,
not an object — translation toward Anthropic MUST parse it, and fail cleanly on
invalid JSON.

### 3.3 DeepSeek native — `POST /v1/chat/completions` + extensions

Same endpoint as 3.2, with extra fields. Use this protocol only when the backend
must expose the specific capabilities; otherwise 3.2 is enough and stays portable.

| Extension | Status | Note |
|---|---|---|
| `reasoning_content` | optional (response) | reasoning kept apart from visible text |
| `reasoning_effort` | optional (request) | reasoning budget |
| `messages[].reasoning_content` | optional | replay or not, depending on the model |
| parallel `tool_calls` | optional | several calls in one message |
| `usage.prompt_cache_hit_tokens` | optional | prefix cache |

Translation rule: `reasoning_content` MUST become a `thinking` block on the
Anthropic side, never visible text. A backend that cannot tell them apart MUST drop
it rather than merge it.

---

## 4. The "protocol proxy" pattern

UHB can sit behind a Claude-first control plane **without patching that control
plane**. This is the LiteLLM pattern, documented and proven (see
[ADAPTERS.md](ADAPTERS.md) §1 and §2).

1. The control plane believes it is talking to Anthropic: it sends
   `POST /v1/messages` with its tools and its system prompt.
2. UHB receives the request, resolves the requested model (`resolve_provider`),
   picks the target protocol.
3. UHB translates to OpenAI `/v1/chat/completions`: `system` → a `system` message,
   `tool_use` blocks → `tool_calls`, `tool_result` blocks → messages with
   `role: "tool"`, `input_schema` → `parameters`.
4. The runtime receives the translated request, executes, answers in OpenAI SSE.
5. UHB translates the response back: `tool_calls` → `tool_use` blocks, `content` →
   `text` blocks, `reasoning_content` → `thinking` blocks, `finish_reason` →
   `stop_reason`.
6. The control plane receives a valid Anthropic stream and never learns the model
   was not Claude.

Three rules separate a proxy that works from a proxy that lies:

- **Tool identifiers survive.** `tool_use.id` and `tool_call.id` must stay stable
  end to end; an orphaned `tool_result` breaks the loop.
- **Finish reasons are translated, not copied.** `length` → `max_tokens`,
  `tool_calls` → `tool_use`, `stop` → `end_turn`. An unknown `stop_reason` MUST be
  mapped to `end_turn` and reported in the logs, never passed through unchanged.
- **The stream stays a stream.** The proxy MUST relay deltas as they arrive.
  Buffering the response in order to translate it breaks the live UI and tool
  streaming, which are the whole point of the contract.

---

## 5. Task states

```
   submit ──▶ queued ──▶ working ──▶ streaming ──▶ done
                          │             │
                          └─────────────┴──▶ failed      (error, timeout, budget)
                                             ▶ cancelled   (cancel())
```

| From | To | Trigger |
|---|---|---|
| `queued` | `working` | the runtime accepts the task |
| `working` | `streaming` | first `text`, `thinking` or `tool_call` |
| `streaming` | `done` | `end(reason: "done")` |
| `working`, `streaming` | `failed` | non-retryable `error`, `timeout` or `budget` |
| `working`, `streaming` | `cancelled` | `cancel()` accepted |

A transition not listed above is a contract error: it MUST be reported, not
absorbed.

**Idempotency.** A task is identified by a `task_id` that is stable from `start` to
`end`. `idempotency_key` (optional, supplied by the control plane) guarantees that a
replay — dropped network, reloaded UI, resent message — does not create a second
turn: the backend either returns the flow of the existing task or an explicit
`duplicate` error. `cancel` and `health` are idempotent by nature. `sync_memory` is
idempotent by content. `load_skill`, `route_model` and `resolve_provider` are pure:
same inputs, same output, no side effect.

---

## 6. Compatibility

Measured in Phase 0 against the public repositories; evidence in
[ADAPTERS.md](ADAPTERS.md).

| Control Plane / Runtime | Support | Integration path | Estimated cost |
|---|---|---|---|
| **Harness Maxxing** | Partial | fake CLI via `MAXXING_CLAUDE_BIN`, or Anthropic proxy (`ANTHROPIC_BASE_URL`) | 1–3 weeks |
| **OpenClaw** | Yes | native `agent harness` plugin (`runAttempt`) or CLI backend (`api.registerCliBackend`) | 1–2 weeks |
| **DeepSeek Harness (DSH)** | Runtime, `experimental` | DSH plugin (secondary path) or UHB-native runtime (primary path) | 2–4 weeks |
| **Hermes** | Partial | extraction of the 8 functions from the gateway and the profiles | 2–3 weeks |

Note — DSH is a special case: already model-agnostic and plugin-based, it does not
need to be made universal. UHB gives it the interface contract that lets any
control plane use it without learning Cordis or its plugin API.

Reading: "Support" qualifies the product's ability to host an external backend
**without being patched**. "Runtime" means the product executes and is a candidate
backend, not a host; "Partial" means a door exists, without a documented execution
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

### Decision: CLI Backend for v0.1, AgentHarnessV2 for v0.2

The CLI Backend is not a downgrade: it covers 5 of the 8 pivot functions
(`execute_task`, `stream`, `cancel`, `health`, `resolve_provider`). The
AgentHarnessV2 adds the 3 remaining functions (`sync_memory`, `load_skill`,
`route_model`).

Two levels of capability, not two versions of stability. The 4 existing
implementations (`extensions/muse`, `copilot`, `acpx`, `codex`) confirm that CLI
Backend is usable as a primary runtime, not a text-only fallback.

---

## 7. Reference implementation

### Decision: Hermes as Runtime + Reference Backend

A reference implementation is being developed at
github.com/Antoine-Thomas/hermes-home-vision, as a runtime. The adapter lives in
`hermes-home-vision/uhb/`, not in this repository. The specification lives here;
the implementation lives there. That repository is currently private: this spec is
public on purpose, so the contract can be reviewed before the code is.

Hermes is not a control plane: it lacks the UI, crons, and notifications layers
that define a control plane. It is a runtime that executes tasks and manages the 8
pivot functions.

---

## 8. Examples

Three complete flows. The first two are buildable today with the measured contracts;
the third is the primary path for DSH and depends on a control plane that does not
exist yet.

### 8.1 Harness Maxxing → UHB → DeepSeek v4.1

```
┌──────────────┐  spawn + stream-json   ┌──────────────┐  HTTP  ┌──────────────┐
│ Harness      │───────────────────────▶│ UHB fake CLI │───────▶│ DeepSeek 4.1 │
│ Maxxing UI   │◀───────────────────────│ (binary)     │◀───────│ /v1/chat/…   │
└──────────────┘  SSE (product side)    └──────────────┘  SSE   └──────────────┘
```

The user clicks Start. Harness Maxxing spawns its runner, which launches the binary
it finds in `MAXXING_CLAUDE_BIN` (here: the UHB adapter):

```bash
uhb-claude -p --output-format stream-json --verbose \
  --model deepseek/deepseek-v4.1-flash \
  --input-format stream-json --session-id 4f1c9a2e
```

The prompt arrives on stdin, one JSONL line, stdin left open for a steer:

```json
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"fix the failing test"}]}}
```

The adapter resolves the model (`resolve_provider`) and calls the runtime. Tools
declared by the control plane are translated from Anthropic `input_schema` to
OpenAI `parameters`:

```json
POST http://127.0.0.1:8101/v1/chat/completions
{"model":"deepseek-v4.1-flash","stream":true,
 "messages":[{"role":"system","content":"You are a coding agent."},
             {"role":"user","content":"fix the failing test"}],
 "tools":[{"type":"function","function":{"name":"bash","parameters":{"type":"object"}}}]}
```

Each delta is translated back to the stream-json line Harness Maxxing expects, so
its normaliser (`sessions/normalize.py`) turns it into a tool card and then text:

```json
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"call_1","name":"bash","input":{"cmd":"pytest -q"}}]},"session_id":"4f1c9a2e"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"The assertion is about…"}]},"session_id":"4f1c9a2e"}
{"type":"result","subtype":"success","is_error":false,"session_id":"4f1c9a2e","usage":{"input_tokens":1842,"output_tokens":233}}
```

The UI shows the tool card, the failing output and the diff, live. Nothing in
Harness Maxxing was patched: it believes it ran `claude`.

### 8.2 OpenClaw → UHB → Hermes

```
Telegram ──▶ ┌──────────┐  execute_task()   ┌──────────┐  HTTP  ┌─────────┐
             │ Gateway  │──────────────────▶│ UHB      │───────▶│ Hermes  │
             │ OpenClaw │◀─ StreamEvent ────│ backend  │◀───────│ runtime │
             └──────────┘                   └──────────┘        └─────────┘
```

The v0.1 path is a CLI backend plugin: OpenClaw owns the turn, the adapter only has
to speak the process contract.

```ts
api.registerCliBackend({
  id: "uhb-cli", modelProvider: "uhb",
  config: {
    command: "uhb-cli",
    args: ["--output-format", "stream-json", "--input-format", "stream-json"],
    output: "jsonl", jsonlDialect: "claude-stream-json",
    sessionArgs: ["--session", "{sessionId}"], sessionMode: "existing",
    serialize: true,
  },
});
```

A Telegram message becomes a gateway turn; the adapter receives the prompt on
stdin and answers by calling Hermes through the 8 functions:

```ts
// inside the adapter, one per turn
const stream = backend.execute_task(message.text, {
  session_id: ctx.sessionId, workspace: "/srv/workspace",
  system: ctx.systemPrompt, tools: ctx.tools.map(toToolSpec),
  model_ref: "hermes/local",
});
for await (const ev of stream) emitStreamJson(ev);   // back to OpenClaw on stdout
```

v0.1 covers 5 of the 8 functions (`execute_task`, `stream`, `cancel`, `health`,
`resolve_provider`) — enough for a working primary runtime. v0.2 moves to
`AgentHarnessV2` and adds `sync_memory`, `load_skill`, `route_model`, so memory and
skills stop being the control plane's private business.

### 8.3 UHB-native control plane → UHB → DSH

```
┌──────────────┐  8 UHB functions   ┌──────────┐  events  ┌──────────────┐
│ UHB-native   │───────────────────▶│ UHB      │─────────▶│ DSH          │
│ control plane│◀── StreamEvent ────│ router   │◀─────────│ agent loop   │
└──────────────┘                    └──────────┘          └──────┬───────┘
                                                         session.jsonl │
                                                            (append-only)
```

This path is hypothetical: the control plane does not exist yet. It is still the
one that shows why DSH matters — no model protocol is translated here, UHB only
carries tasks and events, and DSH's own model adapter talks to whatever endpoint is
configured.

```ts
// the control plane side: the same 8 functions, no translation layer
const task = uhb.execute_task("summarise this repository", {
  session_id: "s-01",
  workspace: "/srv/repo",
  model_ref: "dsh/deepseek-v4.1-flash",
  tools: [],                      // DSH owns the sandbox and its own tools
});
for await (const ev of task) render(ev);

// cancel is a waterfall interruption, not a process kill
await uhb.cancel("t-42");         // → { cancelled: true, already_done: false }
```

Because DSH's session log is append-only, `stream` can replay it and UHB can offer
what a stateless proxy cannot: a task that survives its own transport.

```json
{"seq":1,"type":"start","task_id":"t-42","model_ref":"dsh/deepseek-v4.1-flash"}
{"seq":2,"type":"tool_call","id":"c1","name":"read_file","input":{"path":"README.md"}}
{"seq":3,"type":"tool_result","id":"c1","ok":true,"output":"# UHB — …"}
{"seq":4,"type":"end","task_id":"t-42","reason":"done"}
```

Forking a session is then a log operation, not a product feature: the control plane
replays up to `seq:2` on a branch and continues. The Cordis binding that wires these
eight functions into the DSH plugin API is the adapter's job, and its exact surface
is **[TO VERIFY]** against a pinned DSH version — the log format above is the part
the contract can already pin down.
