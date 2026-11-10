# NON-GOALS — what UHB does not do

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

A contract is worth as much for what it excludes as for what it defines. Every
non-goal below is a decision, not a gap.

---

## 1. UHB does not replace Claude Code for anyone with a Claude Max subscription

UHB does not exist to "liberate" a user who is happy with their Claude
subscription. It exists for the user who wants DeepSeek, Kimi, GLM, a local
endpoint or any model, without being married to a vendor — including a user who
keeps Claude for some tasks and another model for others.

Concrete consequence: no functional parity objective with Claude Code. If a
capability exists only in Claude Code (a proprietary tool, an internal compaction
routine), UHB does not reimplement it.

## 2. UHB does not work around Anthropic's restrictions

Anthropic has explicitly closed the door on routing a non-Claude model through
Claude Max subscriptions. That is Anthropic's decision, taken by the product's
vendor, and it is not a bug.

UHB therefore offers no credential sharing, no quota circumvention, no
authentication-header forgery and no reuse of a subscription token for another
model. What it offers is documented as such: a compatible HTTP endpoint, a
replaceable CLI binary, a native execution plugin. If a product refuses a
configuration, that is for its vendor to decide; UHB's job is to make explicit what
remains open.

## 3. UHB is not a control plane

UHB does not manage product-level sessions, the user interface, scheduled jobs,
notifications, product-level memory, team permissions or billing. All of that
belongs to the control plane.

UHB holds task state in memory — to cancel, to deduplicate, to answer about health
— and nothing more. A UHB backend that restarts loses that state; an in-flight task
is lost, and relaunching it is the control plane's job.

## 4. UHB is not a model

UHB performs no inference. It has no weights, does no tokenisation, computes no
logits, does no RAG, picks no sampling strategy.

It routes toward a model and translates a protocol. A UHB that "improves" a
response by rewriting it is no longer a contract: it is a proxy lying about its
content. The only permitted transformation is structural (formats, fields, finish
reasons), never semantic.

## 5. UHB is not a storage format

UHB negotiates the memory format with the runtime through `sync_memory`; it does
not impose it. It defines neither a database schema, nor a file format, nor a
normative directory tree.

Two backends may store memory differently — one markdown file per level, a SQLite
database, a vector index — and remain conformant, as long as `sync_memory` writes
at the requested level, does not spill into another level, and reports conflicts.

## 6. Other assumed exclusions

- **No code in this repository.** UHB v0.1 is a spec. No adapter, no binary, no
  server. Code comes after the contract is validated.
- **No default model selection.** `route_model` chooses from an intent; UHB does
  not decide on the user's behalf which model is "best".
- **No telemetry.** The contract provides no usage report to a third-party server.
  Token counters stay local, exposed through `usage` in the stream.
- **No graphical interface.** UHB has no UI, no dashboard, no web configuration
  page. The UI belongs to the control plane.
- **No secret management.** The contract carries environment variable **names**,
  never values. A vault, a keyring, a `.env` file: that is the deployment's job,
  not the contract's.

---

## What these exclusions protect

A contract that tries to do everything becomes a product, and a product marries its
ecosystem. Each non-goal above keeps a clean boundary: the control plane decides,
the runtime executes, UHB translates. That boundary is what lets a Claude-first
harness use DeepSeek without rewriting its product, and a local runtime be usable
from three different UIs without knowing any of the three.
