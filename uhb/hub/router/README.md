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

## Measured

`route()` over 2000 calls: p50 0.013 ms, p95 0.020 ms, p99 0.026 ms, mean
0.015 ms. `classify()` p95 0.007 ms. Catalog: 0.50 ms cold, 0.22 ms cached.
The router is never the latency problem.

Live fail-closed run (no pool key in the environment, `dev` mode): the two paid
candidates were skipped by name, local Ollama answered in 685 ms. The same call
without `reasoning_effort: none` took 7.1 s — a one-word reply is not worth a
thinking trace.
