# Reference implementation — the Hermes adapter

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

How the reference implementation of this contract is built. It is a plan: no
adapter code lives in this repository.

**Where the code lives.** `hermes-home-vision/uhb/`, a Python package inside the
Hermes Home Vision repository. The specification lives here; the implementation
lives there. That repository is currently private, which is deliberate: the
contract is published first so it can be reviewed before the code.

---

## 1. Why Hermes is the reference

Hermes is a runtime, not a control plane: it owns the agent loop, tools, memory and
skills, and it lacks the UI, cron and notification layers that define a control
plane. That makes it the cleanest place to implement the contract — every one of the
eight functions already has a working counterpart inside it, so the adapter is an
extraction rather than a rewrite.

It is also the cheapest place to discover that the contract is wrong. A runtime that
implements all eight functions exposes the gaps in days, not after three adapters.

## 2. Package layout

```
hermes-home-vision/
└── uhb/
    ├── __init__.py        # public surface: the eight functions
    ├── contract.py        # types mirroring SPEC.md §1
    ├── backend.py         # UhbBackend: binds the eight functions to Hermes
    ├── task.py            # task registry, lifecycle, idempotency keys
    ├── events.py          # StreamEvent normalisation and stream()
    ├── memory.py          # sync_memory over per-profile memory files
    ├── skills.py          # load_skill over the skills library
    ├── routing.py         # route_model and resolve_provider
    ├── health.py          # health() with no inference
    └── tests/
        ├── test_task.py
        ├── test_events.py
        ├── test_memory.py
        ├── test_skills.py
        ├── test_routing.py
        ├── test_health.py
        └── test_e2e.py
```

One module per concern, one test module per module. `contract.py` is a deliberate
duplicate of the TypeScript types in `SPEC.md` §1 — Python has no import path into a
specification, and a duplicate that is tested against the spec is cheaper than a
generator nobody maintains.

---

## 3. The eight functions

### 3.1 `execute_task`

```python
async def execute_task(prompt: str, ctx: TaskContext) -> AsyncIterator[StreamEvent]
```

**Expected implementation.** Allocate a `task_id`, register it with an
`idempotency_key` → `task_id` map, open the Hermes turn with `ctx.system` as the
system prompt and `ctx.tools` as the tool policy, then forward every loop event
through the normaliser. On exit — normal, error, cancellation — emit exactly one
`end` and unregister the task.

**Tests.**
- a turn with no tool call emits `start`, `text`, `usage`, `end` in that order;
- a turn with a tool call emits `tool_call` then `tool_result` with the same id;
- a second call with the same `idempotency_key` does not start a second turn;
- an exception inside the loop still emits `end` with `reason: "failed"`;
- no event is emitted after `end`.

### 3.2 `sync_memory`

```python
def sync_memory(level: MemoryLevel, content: str) -> MemorySyncResult
```

**Expected implementation.** Resolve the file for the level (workspace, project,
topic), append the block if its hash is absent, report bytes written and the
conflict flag from a stat-before/write-after comparison. Never touch another level.

**Tests.**
- writing the same content twice produces one block and `conflict: false`;
- a concurrent write between read and write reports `conflict: true`;
- content above the configured cap sets `truncated: true` and says so;
- writing at `topic` leaves the `project` file byte-identical.

### 3.3 `load_skill`

```python
def load_skill(skill_id: str) -> SkillDescriptor
```

**Expected implementation.** Read the skill front matter from the skills library,
return identifier, version, description, path and declared requirements. Pure
function, no network, no model.

**Tests.**
- a known skill returns a descriptor whose `path` exists;
- an unknown skill raises `UnknownSkill`, not an empty descriptor;
- no HTTP client is instantiated during the call (monkeypatched socket);
- two calls return equal descriptors.

### 3.4 `route_model`

```python
def route_model(intent: ModelIntent) -> ModelRef
```

**Expected implementation.** Filter the configured providers by capability, context
and locality, sort by cost, return the first with a `rationale` string. Deterministic
given the same configuration.

**Tests.**
- `min_context` filters out providers below it;
- `locality: "local"` never returns a remote provider;
- the same intent and config return the same ref twice;
- an unsatisfiable intent raises `NoModelForIntent` listing what was tried.

### 3.5 `stream`

```python
def stream(output: StreamEvent) -> None
```

**Expected implementation.** Synchronous push onto the turn's queue; the consumer
(the gateway SSE channel) drains it. Never blocks, never reorders.

**Tests.**
- events pushed in order arrive in order;
- pushing after the task closed is a no-op or raises `TaskClosed` — never a crash;
- a slow consumer does not block the producing coroutine (bounded queue + drop
  policy documented).

### 3.6 `cancel`

```python
def cancel(task_id: str) -> CancelResult
```

**Expected implementation.** Look the task up, signal the Hermes loop to stop after
the current tool finishes, mark the task, return immediately. The `end` event comes
from the loop, not from `cancel`.

**Tests.**
- cancelling a running task returns `cancelled: true`;
- cancelling a finished task returns `already_done: true` and does not raise;
- cancelling an unknown id returns `already_done: true`, `reason: "unknown_task"`;
- a cancelled task emits exactly one `end(reason: "cancelled")`.

### 3.7 `health`

```python
def health() -> HealthStatus
```

**Expected implementation.** Version string, provider reachability with a short
timeout each, queue depth. No model call, ever.

**Tests.**
- returns in under 2 s with every provider unreachable;
- `providers` reports `reachable: false` rather than raising;
- no token counter moves during the call (assert on the usage store);
- `queue_depth` matches the registry size.

### 3.8 `resolve_provider`

```python
def resolve_provider(model_ref: str) -> ProviderDescriptor
```

**Expected implementation.** Parse `provider/model`, look it up in the provider
configuration, return protocol, base URL, the **name** of the environment variable
holding the key, and the native model id.

**Tests.**
- a known ref returns a descriptor whose `api_key_env` is a name, not a value;
- an unknown ref raises `UnknownModelRef` listing known refs;
- the returned `base_url` never contains credentials;
- round-trip: `resolve_provider(route_model(intent).model_ref)` succeeds.

---

## 4. Dependencies

- **Hermes gateway** — the execution loop, tool policy and event channel.
- **Skills library** — front matter and resolution (no network).
- **Memory layer** — per-profile files, already written by the product.
- **Provider configuration** — the routing table and key names.
- **Council / RAG** — optional: they are tools from the runtime's point of view, not
  contract surface. If they are exposed, they go through `tools`, not through a
  ninth function.

No new third-party dependency is needed. If the adapter starts requiring one, that
is a signal the boundary is wrong.

## 5. Test plan

| Level | What it proves | Where |
|---|---|---|
| Unit | each function in isolation, including its failure modes | `tests/test_*.py` |
| Contract | event order, single `end`, no inference in `health` | `tests/test_events.py` |
| Integration | one real turn through the gateway with a local model endpoint | `tests/test_e2e.py` |
| Regression | a pinned adapter version against a pinned Hermes version | CI |

The end-to-end test runs against a local OpenAI-compatible endpoint, never a paid
API: the contract must be provable offline.

## 6. Risks

- **Extraction drift.** The gateway may keep growing its own task logic; the
  adapter must call it, not shadow it.
- **Cancellation semantics.** Cooperative cancellation is the hardest part: the
  loop must finish the in-flight tool, and the adapter must not emit `end` twice.
- **Memory negotiation.** Hermes writes memory in its own format; `sync_memory`
  must report conflicts instead of reformatting the file.

## 7. Definition of done

1. The eight functions exist with the signatures above and the tests green.
2. One turn streams end to end through a control plane stub.
3. `cancel` leaves the task in `cancelled` with a single `end`.
4. `health()` answers without inference, in under 2 s.
5. `ADAPTERS.md` §4 records the limitations found while building it.
