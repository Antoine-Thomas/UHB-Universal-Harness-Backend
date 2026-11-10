# Wiki Schema

> Conventions that bind every operation. `ingest` reads this file before it
> compiles anything, `lint` measures it, and `query` assumes it. A page that
> breaks a rule here is not a style opinion: it is a defect `lint` will report.

## Layers

| Layer | Path | Mutability |
| --- | --- | --- |
| Sources | `raw/notes/` | Immutable. Never edited by an operation. |
| Pages | `wiki/<tag-dir>/` | Derived. Rewritten by `ingest`, never hand-edited in bulk. |
| Conventions | `wiki/schema.md` | This file. Changes are deliberate, not incidental. |

Structure files — `index.md`, `log.md`, `schema.md`, `contradictions.md` — live
in `wiki/` but are not pages: they are never linted as content and never
returned by `query`.

## Frontmatter

Every page starts with this block, in this key order:

```yaml
---
title: "Page name"
tags: [concept, entity, procedure, reference]
sources: ["raw/notes/source-1.md"]
created: "2026-11-10"
updated: "2026-11-10"
contested: false
---
```

Mandatory keys: `title`, `tags`, `sources`, `created`, `updated`, `contested`.
`source_hashes` is added by `ingest` and records, per source, the sha256 of the
exact content that was compiled. It is what makes re-ingestion a no-op.

- `created` is set once and never moved. `updated` moves on every rewrite.
- `sources` is a union: a page compiled from three notes lists three notes.
- `contested: true` means `contradictions.md` holds an entry naming this page.
  `ingest` preserves the flag; `lint --contradictions` sets it.

## Rules

1. One page = one concept, one entity, or one procedure. Never one page per source.
2. At least two `[[wikilinks]]` per page. A page with no entry point is dead weight.
3. At most 200 lines per page. Beyond that, split and link the parts.
4. Tags come from the closed taxonomy below. An unknown tag is an error, not a hint.
5. Every new or updated page is listed in `index.md`, in its tag section, by slug.
6. Every operation appends one entry to `log.md`. The log is append-only.
7. File names are slugs: lowercase, hyphens, no spaces (`fallback-chain.md`).

## Tag taxonomy

| Tag | Directory | Use for |
| --- | --- | --- |
| `concept` | `concepts/` | An idea the system explains (the router, the contract). |
| `entity` | `entities/` | A thing that exists (a service, a model, a host). |
| `procedure` | `procedures/` | An ordered way to do something, reproducible. |
| `reference` | `references/` | A stable lookup table, format or external pointer. |

Adding a tag is a schema change: it touches `TAXONOMY` in `scripts/__init__.py`,
`TAG_DIRS`, `TAG_SECTIONS` and this table, in the same commit.

## Wikilinks

`[[slug]]` targets a page by file name, without directory and without extension.
`[[slug|display text]]` is accepted and displayed by the reader, not by `lint`.
Reserved names (`[[index]]`, `[[log]]`, `[[schema]]`, `[[contradictions]]`) are
structural pointers: they are not broken links, but they do not count as inbound
links either — a page linked only from `index.md` is still an orphan.
