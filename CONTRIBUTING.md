# CONTRIBUTING

![Status](https://img.shields.io/badge/status-v0.1%20draft-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)

Two kinds of contribution, two procedures. Neither accepts code in this repository
until the contract is validated.

---

## Proposing an adapter

1. **Open an issue** titled `Adapter: <control plane name>`. Answer in three lines:
   which execution interface the product really exposes, which door is usable,
   what is missing.
2. **Describe the integration path**, one of the three known ones:
   - **native** — the product exposes an execution registration interface
     (e.g. `registerAgentHarness`, `registerCliBackend`);
   - **proxy** — the product only speaks one dialect (often Anthropic
     `/v1/messages`) and accepts a `base_url` or an environment variable;
   - **fake CLI** — the product launches a binary by name or by environment
     variable, and consumes its output.
3. **Provide a document** `ADAPTERS/<name>.md` with: the contract actually
   provided, the evidence (file paths, docs read, version), the chosen path, what
   the adapter must supply, estimated effort, risk and its cause.
4. **Open the PR with the doc alone.** Code comes after validation, in a separate
   repository.

An adapter note without measured evidence is not merged. A plausible but
unverified claim is marked `[TO VERIFY]` — that is acceptable and useful; an
invented claim is not.

## Proposing a spec change

1. **Open an issue** titled `Spec: <topic>`.
2. **Describe** the real use case that motivates the change, the impact on the
   eight pivot functions, and what breaks if it is not done.
3. **Open the PR** with the diff of `SPEC.md` only. A change that touches
   `ARCHITECTURE.md` or `ADAPTERS.md` in the same PR must justify it explicitly.

A breaking change means a minor version bump (`v0.1` → `v0.2`) and an updated
"Compatibility" section.

## Ground rules

- **No secrets, no local paths.** No token, no key, no personal path in a
  document. Key examples are variable **names** (`DEEPSEEK_API_KEY`), never
  values.
- **No vendor name in the contract.** The contract speaks in intents and model
  references. An example may cite a vendor; a rule may not.
- **Measure before asserting.** Control plane behaviour is proven by a file or an
  up-to-date doc. A third-party page or mirror may be stale: say so when you
  depart from it.
- **One rule per paragraph.** A contract gets re-read; a sentence carrying two
  obligations will be implemented wrongly.

## Document format

Markdown, English, short sentences. **MUST**, **SHOULD**, **MAY** in the RFC 2119
sense inside `SPEC.md`. Code blocks are contract examples (TypeScript by
preference), not an implementation.

## Contribution license

MIT, inbound = outbound (see [LICENSE](LICENSE)). By opening a PR you agree that
your text is published under that license. No CLA is required.
