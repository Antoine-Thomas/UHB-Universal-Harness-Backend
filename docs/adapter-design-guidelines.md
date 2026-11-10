# Adapter design guidelines

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

Rules for anyone writing an adapter for this contract. They exist because the first
four adapters will disagree with each other in exactly the places where a rule is
missing.

---

## 1. One adapter, one product

An adapter binds the contract to **one** control plane or **one** runtime. Not two,
not "both sides". A component that translates between two products is a bridge, and
bridges hide which side owns a decision.

If a product can be used both ways (OpenClaw as a host and Hermes as a runtime), that
is two adapters with separate versioning.

## 2. An adapter exposes the eight functions

All eight, always. A function the product cannot support returns
`unsupported` with a reason — it does not disappear, and it never returns a fake
success. A missing function is indistinguishable from a bug at the caller.

## 3. An adapter never patches the host

No fork, no vendored copy, no monkeypatch of the product's internals. The adapter
lives outside and uses documented hooks: a plugin registration, an environment
variable, an HTTP endpoint.

When the only door is undocumented, say so in `ADAPTERS.md` and treat it as a
risk — not as a contract. Harness Maxxing's `MAXXING_CLAUDE_BIN` is acceptable
because the product's own test suite uses it; `ANTHROPIC_BASE_URL` is not, because
the product lists it as reserved.

## 4. Pin what you test against

State the exact version of the product the adapter was validated on, in
`ADAPTERS.md`. Re-run the suite on every bump, and fail the build when the pinned
version is no longer installable.

Experimental surfaces (OpenClaw `AgentHarnessV2`, DSH plugins) get a second pin for
the plugin API itself.

## 5. Document the limitations, in the shared file

Every adapter carries a section in `ADAPTERS.md` with: contract actually provided,
evidence (file paths, docs, version), chosen path, effort, risk and its cause. An
adapter without that section is not mergeable, however well it works.

A limitation is a fact, not a failure: "tools are not available on this path" is
useful; silence is not.

## 6. No secrets, no local paths

The adapter reads keys from the environment by **name**. It never logs a token, never
writes a credential into a session log, and never puts a personal path in a
document.

## 7. Failure is explicit

- Unknown model → `unknown_model_ref`, with the list of known refs.
- Unsupported function → `unsupported`, with a reason.
- Stream cut → `end` with `failed` or `cancelled`, never silence.
- Timeout → `end(reason: "timeout")`, and the runtime's process is reaped.

A caller must be able to distinguish "the model said nothing" from "the adapter
died".

## 8. Cancellation is cooperative and idempotent

`cancel(task_id)` asks; it does not kill. The runtime finishes the in-flight tool,
then emits the single `end(reason: "cancelled")`. Cancelling twice, or cancelling a
finished task, returns `already_done: true` and succeeds.

An adapter that kills a process group directly is not conformant: it loses tool
results and leaves child processes behind.

## 9. The stream is ordered and closed once

`start` first, `end` last, nothing after. Every event carries `at`. If the adapter
buffers, it must buffer *within* the stream — never replay a finished turn as if it
were live.

## 10. Test matrix

| Case | Required |
|---|---|
| happy turn, no tools | yes |
| turn with a tool call and a result | yes |
| unknown model reference | yes |
| cancel mid-stream | yes |
| idempotent replay with the same key | yes |
| product version pinned and asserted | yes |
| `health()` with a provider down | yes |
| offline (no paid API) | yes |

## 11. Naming and placement

- Adapter code lives in the product's repository, not here.
- `ADAPTERS.md` gains a section per adapter; the section number is stable once
  published, because other documents link to it.
- Issues are titled `Adapter: <product>`, one per adapter, one label per capability
  level (`experimental` when the product's surface is).
