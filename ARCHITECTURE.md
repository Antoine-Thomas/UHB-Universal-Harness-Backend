# ARCHITECTURE — UHB

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

How the three layers fit together, what moves between them, where a new component
plugs in, and what never changes.

---

## 1. The three layers

```
┌─ CONTROL PLANE ─────────────────────────┐
│ Harness Maxxing · OpenClaw · Hermes     │
│ (UI, sessions, crons, memory)           │
└────────────────┬────────────────────────┘
                 │ HTTP (Anthropic or OpenAI)
                 ▼
┌─ UHB (Universal Harness Backend) ──────┐
│ resolve_provider(model_ref)             │
│ stream() · cancel() · health()          │
│ Anthropic ↔ OpenAI translation          │
└────────────────┬────────────────────────┘
                 │ HTTP (native protocol)
                 ▼
┌─ RUNTIME ───────────────────────────────┐
│ DeepSeek v4.1 · Kimi · GLM · Claude     │
│ Ollama · OpenAI · local                 │
└─────────────────────────────────────────┘
```

**Control Plane.** It owns product state: which session, which project, which
policy, which cron, which notification. It decides *what* to do and *for whom*. It
does not know which protocol the model speaks, and that is deliberate: that is what
lets you change models without changing the UI.

**UHB.** It owns translation: a model intent becomes a reference, a reference
becomes an endpoint and a protocol, a request in one dialect becomes a request in
another, a proprietary stream becomes a normalised stream. It also owns the task
lifecycle: `queued`, `working`, `streaming`, `done`, `failed`, `cancelled` (see
[SPEC.md](SPEC.md) §5).

**Runtime.** It owns execution: the model loop, tool calls, context, compaction,
prefix caches. It knows nothing of product sessions or UI. A runtime that exposes
`/v1/chat/completions` can already be plugged in; so can one that exposes
`/v1/messages`.

**Why three layers and not two.** Merging UHB into the runtime makes the runtime
responsible for routing: every new model would force a runtime redeploy. Merging
UHB into the control plane makes the control plane responsible for protocol: every
new model would force a product redeploy. The thin middle layer is the one that
changes most often, and it is exactly the one we want to be able to swap without
breaking anything.

---

## 2. The request flow

From the UI down to the model, in six steps. Example: the user types a message in
a session whose model is `deepseek/deepseek-v4.1-flash`.

1. **The control plane assembles the turn.** It picks the session, the project, the
   system prompt, the tool policy and the memory. It produces a `TaskContext` and a
   user prompt. Nothing has hit the network yet.
2. **The control plane calls the backend.** Depending on its real interface it
   calls `execute_task` (native plugin), or sends `POST /v1/messages` to a local
   proxy (fake CLI / compatible endpoint). That is the only difference between the
   two paths: the contract is the same.
3. **UHB resolves the model.** `route_model` if the control plane did not impose a
   reference, then `resolve_provider`: protocol, `base_url`, `api_key_env`,
   `model_id`. The key is read from the environment at the last moment and never
   carried by the contract.
4. **UHB translates the request.** If the control plane speaks Anthropic and the
   runtime speaks OpenAI, `tool_use`/`tool_result` blocks become `tool_calls` and
   `role: "tool"` messages; `system` becomes a system message; `input_schema`
   becomes `parameters`. If the control plane already speaks the right dialect,
   this step is a pass-through.
5. **The runtime executes and streams.** It emits its native flow (OpenAI SSE,
   Anthropic SSE, CLI JSONL). Every delta of text, reasoning, tool call and result
   is an event.
6. **UHB translates back and normalises.** Each delta becomes a typed
   `StreamEvent`, pushed by `stream()` in arrival order. The control plane renders
   it: tokens being written, tool cards, diffs. At the end, `end` closes the task
   and freezes its state.

Two paths are legitimate and produce the same observable result:

- **Plugin path** — the control plane exposes an execution interface (OpenClaw
  `registerAgentHarness` / `registerCliBackend`). The adapter registers there and
  the eight functions are called directly.
- **Proxy path** — the control plane only knows how to talk to Claude. UHB listens
  on HTTP at `127.0.0.1:PORT` and imitates `/v1/messages`. Not a line of the
  control plane is modified; this is the Harness Maxxing path, and the path of any
  product that hardcodes `claude`.

---

## 3. Extension points

Three axes, independent. One can be added without touching the others.

### 3.1 Adding a control plane

1. Write `ADAPTERS/<name>.md`: real interface, integration path, effort, risk.
2. Pick the path: native (plugin), proxy (compatible endpoint), or replaceable CLI.
3. Implement the eight functions in the control plane's dialect. An unsupported
   function returns `unsupported`, never a fake success.
4. Prove one complete turn: a prompt, a tool call, a diff, a `cancel`.

What does not change: neither the contract, nor the protocols, nor the state
machine.

### 3.2 Adding a runtime

1. Check the real protocol: `/v1/messages`, `/v1/chat/completions`, or a bespoke
   dialect.
2. Add an entry to the resolution table: `model_ref` → protocol, `base_url`,
   `api_key_env`, `model_id`.
3. Declare the real capabilities: tools? incremental streaming? separate
   reasoning? vision? maximum context?
4. Verify `health()` without inference: a `GET /health` or `GET /v1/models`.

What does not change: the contract stays the eight functions; a runtime never needs
to know the control plane.

### 3.3 Adding a protocol

1. Document the request/response schema in [SPEC.md](SPEC.md) §3, with required and
   optional fields.
2. Provide both translations: toward OpenAI (the pivot), and from Anthropic (the
   most common inbound dialect).
3. Define the finish-reason and tool-identifier mapping.
4. Document what is lost: a protocol that does not carry separate reasoning must not
   present it as text.

What does not change: the pivot stays OpenAI `/v1/chat/completions`, because that is
what most providers implement.

---

## 4. Invariants

What stays true whatever the adapter, runtime or protocol. A component that breaks
an invariant is not conformant, even if it "works".

1. **No secret in the contract.** `resolve_provider` returns an environment
   variable **name**, never a value. The contract carries no key, token or cookie.
2. **No inference outside `execute_task`.** `health`, `route_model`, `cancel`,
   `load_skill`, `resolve_provider` spend no tokens. A model must not be billed for
   answering "I am fine".
3. **One ordered stream, one `end`.** `start` first, `end` last, never an event
   after. A stream cut without `end` is a contract error, not a normal finish.
4. **The task is the unit of cancellation.** `cancel(task_id)` does not kill an
   arbitrary process: it asks the named task to stop, cooperatively, and the final
   state is `cancelled`.
5. **The control plane's protocol does not change.** A control plane that speaks
   Anthropic keeps speaking Anthropic. UHB adapts, never the host product.
6. **UHB holds no product state.** No session, no cron, no product memory, no
   notification. It may hold task state in memory for cancellation and idempotency,
   and nothing more.
7. **Failure is explicit.** An unsupported function returns `unsupported`. An
   unresolvable model returns `unknown_model_ref`. An invalid stream returns an
   error. Silence and fake success are forbidden.
8. **Memory is negotiated, not imposed.** `sync_memory` writes at the requested
   level and reports conflicts; it never reorganises another level's memory.

---

## 5. Typical deployment

```
workstation / server
├── control plane (web UI, port 3000)
├── UHB proxy (127.0.0.1:8101, Anthropic-compatible)
└── runtime
    ├── remote  → https://api.deepseek.com/v1     (key in the env)
    └── local   → http://127.0.0.1:8000/v1        (self-hosted engine)
```

Three points of attention, drawn from the measured state of the art:

- **Listen on loopback only.** A proxy that translates Anthropic ↔ OpenAI exposes
  an execution surface; defaulting to `0.0.0.0` is a mistake.
- **Keep the declared context window in sync.** If the control plane believes in
  200K and the runtime accepts 128K, compaction fires too late and every turn
  fails (a real case documented on the DeepSeek V4.1 side).
- **A prefix cache changes everything in agentic use.** Every tool turn resends the
  whole history: without prefix reuse, cost and latency explode by the 20th turn.
  The contract imposes nothing, but a backend SHOULD expose the capability when the
  runtime has it.

---

## 6. When it breaks

Three failures recur in every project of this kind; naming them here saves
rediscovering them.

- **A stream with no `end`.** The control plane sees tokens, then nothing: the UI
  stays "running" forever. The backend MUST close the task (`failed` or
  `cancelled`) even when the runtime dies without saying so.
- **An orphaned `tool_result`.** The call identifier changed while crossing a
  protocol; the runtime no longer recognises its own result and loops. Translation
  MUST preserve the identifier, never regenerate it.
- **A wrong declared context window.** The control plane believes in 200K, the
  runtime accepts 128K: the last turns all fail and compaction fires too late. The
  value published by `health()` MUST be the runtime's real value.

In all three cases the rule is the same: make the failure visible — an `error`
event, a terminal state, a measured value — rather than letting it turn silently
into strange behaviour.
