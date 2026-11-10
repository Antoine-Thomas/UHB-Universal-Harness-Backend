# UHB — Universal Harness Backend

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

This project originates from a French-speaking author. The specification is in
English to reach the widest audience; discussions and issues can be in either
language.

**UHB is the interface contract that lets any Control Plane use any execution
backend, whatever the model. It makes Claude-first harnesses work with DeepSeek,
Kimi, GLM, OpenAI, and any future model.**

Status: **v0.1 — spec only**. This repository contains a contract, not an
implementation. No code, no binary, no adapter shipped.

---

## 1. The problem

The agent control planes that matter today — Harness Maxxing, OpenClaw, Hermes —
were built around Claude Code. That is not a technical decision, it is history:
Claude Code shipped the first agent CLI that was usable in production, with
persistent sessions, tool calls, streaming and compaction. Harnesses therefore got
used to launching `claude`, consuming its `stream-json` format and treating its
vocabulary as a de facto standard. The result: a user who owns DeepSeek v4.1 or
Kimi has no clean path. Every harness has its own notion of an "executable model",
when it has one at all, and each one is different.

Anthropic has also explicitly closed the door on routing a non-Claude model
through Claude Max subscriptions. That is Anthropic's decision, not a bug to work
around: any integration that assumes a Claude Max subscription in order to run
DeepSeek is a dead end, and UHB does not offer it. What remains — and what this
contract makes explicit — are the legitimate paths: a compatible HTTP endpoint, a
replaceable CLI binary, a native execution plugin.

Some runtimes, like DeepSeek Harness, are already model-agnostic and plugin-based.
They still lack a standard interface contract for control planes: UHB fills that
gap for DSH, OpenClaw, Harness Maxxing, and future runtimes alike.

## 2. The solution

Two artefacts, and nothing else:

1. **A standard contract** — eight pivot functions ([SPEC.md](SPEC.md) §2) that
   describe everything a control plane asks of an execution backend: run a task,
   stream it, cancel it, sync memory, load a skill, route a model, resolve a
   provider, report health.
2. **One adapter per control plane** ([ADAPTERS.md](ADAPTERS.md)) — the piece that
   translates the contract into the control plane's real interface: HTTP proxy,
   replaceable CLI, or native plugin.

A backend that implements the eight functions is usable from any control plane
that has an adapter. A control plane that has an adapter can use any backend. The
cartesian product is the goal.

## 3. The three layers

| Layer | Role | Examples |
|---|---|---|
| **Control Plane** | decides, governs, supervises: UI, sessions, crons, memory, notifications | Harness Maxxing, OpenClaw, Hermes |
| **UHB** | translates and arbitrates: model routing, protocol translation, normalised streaming, cancellation, health | this contract |
| **Runtime** | executes: calls the model, runs the tools, returns events | DeepSeek v4.1, Kimi, GLM, OpenAI, Ollama, Claude |

Flows, extension points and invariants are detailed in
[ARCHITECTURE.md](ARCHITECTURE.md).

### The eight functions, one line each

| # | Function | What it settles |
|---|---|---|
| 1 | `execute_task` | run a turn and receive its events as a stream |
| 2 | `sync_memory` | write memory at the right level (workspace, project, topic) |
| 3 | `load_skill` | make a procedure available to the model |
| 4 | `route_model` | pick a model from an intent |
| 5 | `stream` | guarantee a stream a live UI can use |
| 6 | `cancel` | stop a running task cleanly |
| 7 | `health` | report backend status without spending tokens |
| 8 | `resolve_provider` | turn a model name into endpoint, protocol, key |

## 4. What UHB is not

UHB is not a control plane, not a runtime, not a model, not a storage format, and
not a way around Anthropic's restrictions. The full list, with reasons, is in
[NON-GOALS.md](NON-GOALS.md).

The acronym is **UHB** — Universal Harness **Backend**. The *Runtime* hosts and
survives (processes, sandbox, crash recovery); the *Backend* executes and is
swappable. This is Backend. The repository is named out in full,
`UHB-Universal-Harness-Backend`, and not `universal-harness-*`: the acronym **UHP**
(Unified Harness Protocol) already exists and names a nearby concept that must not
be confused with this one.

## 5. Who it is for

**Control plane developers.** If your harness launches `claude` in hardcoded form
and you want to stop without rewriting your product, one adapter is enough: the
contract describes what to expose, not how to reorganise your code.

**Non-Claude model users.** If you pay for DeepSeek, Kimi, GLM or a local
endpoint, you should not depend on the vocabulary of a competing vendor. The
contract speaks in intents and model references, not in vendor names.

**Alternative harness maintainers.** If you are reimplementing an agent loop in
Python, Go or Rust, the contract defines the minimum surface to expose in order to
be usable from existing control planes — and testable without a UI.

## 6. How to contribute

One adapter per PR, docs first: code comes after the contract is validated. A spec
change goes through an issue and then a diff of `SPEC.md`. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## 7. References

- **Harness Maxxing** — https://github.com/makushingu/harness-maxxing — Fair
  Source (source-available, not OSI): self-hosted control plane built on the Claude
  Code CLI. Launches `claude -p --output-format stream-json --verbose`, keeps the
  binary replaceable through `MAXXING_CLAUDE_BIN`.
- **OpenClaw** — https://github.com/openclaw/openclaw — MIT: Gateway plus two
  documented execution surfaces (`api.registerCliBackend` and
  `api.registerAgentHarness`), and an optional MCP bridge for tools.
- **DeepSeek V4.1 (proxy pattern)** —
  https://github.com/shi3z/deepseekv4.1-A100-custom — `examples/claude_code/`:
  Claude Code → LiteLLM → inference engine, Anthropic ↔ OpenAI translation.
- **LiteLLM** — https://github.com/BerriAI/litellm — the proven reference for
  Anthropic ↔ OpenAI protocol translation.
- `docs.openclaw.kr/gateway/cli-backends` — third-party mirror of the OpenClaw
  documentation. It describes CLI backends as "text-only, fallback only"; the
  upstream repository docs say the opposite (primary backend, tools through the MCP
  bridge, JSONL). Measured gaps are listed in [ADAPTERS.md](ADAPTERS.md) §2.

## 8. Repository status

What exists today: the spec, and nothing else.

| File | Role |
|---|---|
| [SPEC.md](SPEC.md) | the normative contract: 8 functions, 3 protocols, state machine |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the 3 layers, the request flow, extension points, invariants |
| [ADAPTERS.md](ADAPTERS.md) | measured state of every known control plane and runtime |
| [NON-GOALS.md](NON-GOALS.md) | what UHB refuses to do, and why |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to propose an adapter or a spec change |

What does not exist yet, and will come after the contract is validated: a
reference backend (the 8 functions over an OpenAI-compatible runtime), an
Anthropic ↔ OpenAI proxy, and a first adapter — OpenClaw, because it is the only
one whose execution contract is documented as a contract.

Suggested reading order: this README, then `SPEC.md` §2, then `ARCHITECTURE.md`,
then `ADAPTERS.md`. The rest is context.

**Reference implementation.** Hermes Home Vision
(github.com/Antoine-Thomas/hermes-home-vision) is the reference implementation of
this contract as a runtime: the adapter lives in `hermes-home-vision/uhb/`, not
here. The specification lives in this repository; the implementation lives there.
See [SPEC.md](SPEC.md) §7.

A remark about the contract goes through an issue titled `Spec: <topic>`; a new
control plane through an issue titled `Adapter: <name>`. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE). The contract is free to reimplement; the trademarks,
licenses and registered marks of the control planes cited above remain those of
their authors. This repository contains and redistributes none of their code.
