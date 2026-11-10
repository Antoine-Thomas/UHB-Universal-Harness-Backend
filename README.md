# UHB — Universal Harness Backend

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

This project originates from a French-speaking author. The specification is in
English to reach the widest audience; discussions and issues can be in either
language.

**UHB is the interface contract that lets any Control Plane use any execution
backend, whatever the model. It makes Claude-first harnesses work with DeepSeek,
Kimi, GLM, OpenAI, and any future model.**

## What is this?

A specification, not a product: eight functions, three protocols and one state
machine that describe how a control plane asks a runtime to execute a task. No
code, no binary, no adapter shipped — the contract is the deliverable.

## Why does it exist?

The agent control planes that matter today — Harness Maxxing, OpenClaw, Hermes —
were built around Claude Code. Not by design: Claude Code shipped the first agent
CLI usable in production, so harnesses learned to launch `claude`, consume its
`stream-json` format and treat its vocabulary as a de facto standard. A developer
who owns DeepSeek v4.1 or Kimi has no clean path, because every harness has its own
notion of an "executable model". Anthropic has since explicitly closed the door on
routing a non-Claude model through Claude Max subscriptions — a vendor decision, not
a bug to work around — so what remains are the legitimate paths this contract makes
explicit: a compatible HTTP endpoint, a replaceable CLI binary, a native execution
plugin. Some runtimes, like DeepSeek Harness, are already model-agnostic and
plugin-based; they still lack a standard interface contract for control planes.

## How does it work?

```
┌─ CONTROL PLANE ─────────────────────────┐
│ Harness Maxxing · OpenClaw · Hermes     │
│ (UI, sessions, crons, memory)           │
└────────────────┬────────────────────────┘
                 │ HTTP (Anthropic or OpenAI)
                 ▼
┌─ UHB ───────────────────────────────────┐
│ resolve_provider · stream · cancel      │
│ Anthropic ↔ OpenAI translation          │
└────────────────┬────────────────────────┘
                 │ HTTP (native protocol)
                 ▼
┌─ RUNTIME ───────────────────────────────┐
│ DeepSeek v4.1 · Kimi · GLM · Claude     │
└─────────────────────────────────────────┘
```

| Layer | Role | Examples |
|---|---|---|
| **Control Plane** | decides, governs, supervises | Harness Maxxing, OpenClaw, Hermes |
| **UHB** | translates, routes, normalises | this contract |
| **Runtime** | executes the model loop and tools | DeepSeek, Kimi, GLM, Ollama, Claude |

An adapter is the piece that exposes the contract inside one control plane; a
backend implements it on top of one runtime. A backend that implements the eight
functions works from any control plane that has an adapter — the cartesian product
is the goal. Flows, extension points and invariants are in
[ARCHITECTURE.md](ARCHITECTURE.md); the three worked examples are in
[SPEC.md](SPEC.md) §8.

## Who is it for?

**Control plane developers.** If your harness launches `claude` in hardcoded form
and you want to stop without rewriting your product, one adapter is enough: the
contract says what to expose, not how to reorganise your code.

**Non-Claude model users.** If you pay for DeepSeek, Kimi, GLM or a local endpoint,
you should not depend on the vocabulary of a competing vendor. The contract speaks
in intents and model references, not vendor names.

**Runtime maintainers.** If you are building an agent loop in Python, Go or Rust,
the contract defines the minimum surface to expose in order to be usable from
existing control planes — and testable without a UI.

## What is the status?

- **v0.1 — draft.** The eight functions, three protocols and the state machine are
  written and reviewed; they can still change before v1.0.
- **A reference implementation is being developed** at
  github.com/Antoine-Thomas/hermes-home-vision (currently private). The spec is
  public on purpose: the contract is published so it can be reviewed before the
  code is.
- **3 adapters planned**, tracked as issues: Harness Maxxing (fake CLI), OpenClaw
  (CLI backend, then AgentHarnessV2), DeepSeek Harness (Cordis plugin).

| File | Role |
|---|---|
| [SPEC.md](SPEC.md) | normative contract: 8 functions, 3 protocols, states, examples |
| [ARCHITECTURE.md](ARCHITECTURE.md) | layers, request flow, extension points, invariants |
| [ADAPTERS.md](ADAPTERS.md) | measured state of each product, with evidence |
| [NON-GOALS.md](NON-GOALS.md) | what UHB refuses to do, and why |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to propose an adapter or a spec change |
| [docs/reference-implementation.md](docs/reference-implementation.md) | how the Hermes adapter will be built, function by function |
| [docs/adapter-design-guidelines.md](docs/adapter-design-guidelines.md) | rules every adapter must follow |

## Adopters

Organisations and projects using UHB:

- _Your project here_ — open a PR to add yourself.

## Roadmap

- [ ] **v0.1** — complete spec (in progress)
- [ ] **v0.2** — reference implementation adapter for Hermes
- [ ] **v0.3** — first external adapter (Harness Maxxing or OpenClaw)
- [ ] **v1.0** — 2 external adapters working, spec frozen

## Related work

- **Unified Harness Protocol (UHP)** — a neighbouring standard with a close name
  and a related goal. UHB is not a rename of it: the acronyms are distinct on
  purpose, and this repository is named out in full,
  `UHB-Universal-Harness-Backend`, to avoid the confusion.
- **DeepSeek Harness (DSH)** — MIT, "everything is a plugin" through Cordis,
  model-agnostic by construction, with an append-only replayable session log. A
  runtime, not a control plane; UHB supplies the interface contract it lacks.
- **LiteLLM** — the proven Anthropic ↔ OpenAI protocol proxy. UHB reuses that
  pattern rather than inventing a translator.
- **OpenClaw CLI Backend** — the only product here whose execution contract is
  documented as a contract (`registerCliBackend`, `registerAgentHarness`).
- **Harness Maxxing** — Fair Source, self-hosted control plane on the Claude Code
  CLI; its sanctioned extension point is a replaceable binary
  (`MAXXING_CLAUDE_BIN`).
- **DeepSeek V4.1 proxy example** —
  github.com/shi3z/deepseekv4.1-A100-custom, `examples/claude_code/`: Claude Code →
  LiteLLM → inference engine, the pattern this contract formalises.

Measured details, including a third-party OpenClaw documentation mirror that turned
out to be stale, are in [ADAPTERS.md](ADAPTERS.md).

## How do I get involved?

One adapter per PR, docs first: the code comes after the contract is validated. An
adapter proposal is an issue titled `Adapter: <name>` plus an `ADAPTERS/<name>.md`
with measured evidence; a spec change is an issue titled `Spec: <topic>` plus a diff
of `SPEC.md`. See [CONTRIBUTING.md](CONTRIBUTING.md). Issues that need help are
labelled `help-wanted`.

## License

MIT — see [LICENSE](LICENSE). The contract is free to reimplement; the trademarks,
licenses and registered marks of the products cited above remain those of their
authors. This repository contains and redistributes none of their code.
