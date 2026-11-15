# hub/router — intent in, model out, never a silent failure

Three steps, no hidden state:

1. `classify(request)` — scores surface signals (length, code markers,
   multi-step verbs, file paths, several questions) into `trivial` / `standard` /
   `complex`. Deterministic, no model call: measured p95 **0.014 ms**.
2. `route(request, mode)` — starts at the mode's tier and walks `eco` → `free` →
   `paid`, keeps the models that declare support for that complexity, and drops
   the `paid` tier unless the mode sets `allow_paid: true`.
3. `execute(decision, prompt)` — first candidate that answers wins.

## Two sources of truth, on purpose

| What | Where |
| --- | --- |
| How to reach a provider (protocol, base URL, key **name**) | `uhb/core/router.py`, `uhb/config.yaml` |
| When to choose a model (tier, supported complexity) | `router/catalog.json` |

A catalog entry whose reference the provider table cannot resolve is refused at
load time. Adding a vendor means editing the provider table; adding a model to
the policy means one line in the catalog.

## Fallback policy (fail-closed)

- a remote candidate whose key variable is absent is **skipped**, not called:
  a missing key costs a skip instead of a 401
- a candidate that raises is **recorded and skipped**: the reason kept is the
  exception class, never a payload or a header
- if every candidate fails, `AllProvidersFailed` is raised with the attempts —
  the router never returns an empty string that reads like an answer
- if no model in the allowed tiers supports the complexity, `UnroutableRequest`
  says which tiers were allowed and that `allow_paid` would widen them

## Free models and quotas

The shipped catalog carries six `free` candidates behind two providers
(`NOUS_API_KEY`, `OPENROUTER_API_KEY`). A free model is not a hidden route: it is
a normal candidate that happens to cost nothing, declared like any other.

| Model | Provider | Context | Declared quota |
| --- | --- | --- | --- |
| `meituan/longcat-2.5:free` | `nous` | 1 048 576 | 20 RPM · 50 RPD |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | `openrouter` | 1 000 000 | 20 RPM · 50 RPD |
| `recursive/flint-v1.0:free` | `openrouter` | 262 144 | 20 RPM · 50 RPD |
| `upstage/solar-mini4:free` | `nous` | 524 288 | 20 RPM · 50 RPD |
| `google/gemma-4-31b-it:free` | `openrouter` | 262 144 | 20 RPM · 50 RPD |
| `stepfun/step-3.7-flash:free` | `nous` | 262 144 | 20 RPM · 50 RPD |

`limits` is a policy ceiling, not a measurement: the provider advertises the real
window per response in `x-ratelimit-*`. A `429` is an ordinary failure — recorded
by exception class, then skipped, and the chain continues.

`free_first: true` on a profile moves the `free` tier to the front of the chain,
before the mode's own tier. It is independent of `allow_paid`: reordering never
opens the paid tier.

## Measured

`route()` over 2000 calls: p50 0.013 ms, p95 0.020 ms, p99 0.026 ms, mean
0.015 ms. `classify()` p95 0.007 ms. Catalog: 0.50 ms cold, 0.22 ms cached.
The router is never the latency problem.

Live fail-closed run (no pool key in the environment, `dev` mode): the two paid
candidates were skipped by name, local Ollama answered in 685 ms. The same call
without `reasoning_effort: none` took 7.1 s — a one-word reply is not worth a
thinking trace.
