# hub/wiki — the compiled knowledge base

The HUB keeps a wiki, not a transcript: sources stay immutable, pages are
compiled from them, and a schema binds both — the Karpathy LLM-wiki pattern,
with no domain baked in and no state outside the files.

## Three layers

| Layer | Path | Rule |
| --- | --- | --- |
| Sources | `raw/notes/` | Immutable. Drop a note here, never edit it afterwards. |
| Pages | `wiki/<tag>/` | Derived. `ingest` writes them, a machine owns the format. |
| Schema | `wiki/schema.md` | The conventions: read by `ingest`, enforced by `lint`. |

`index.md` is the catalogue, `log.md` the append-only journal,
`contradictions.md` the register of what the pages disagree about.

## Three operations

```python
from uhb.hub.wiki import ingest, query, lint

ingest("raw/notes/note.md")      # one LLM call: one source -> N pages
query("how does routing work?")  # index -> candidate pages + a confidence
lint()                           # orphans, dead links, index gaps, bad frontmatter
```

`ingest` is idempotent: the sha256 of a source is stored in the frontmatter of
every page compiled from it, so re-ingesting an unchanged note rewrites nothing.
`query` never calls a model — it returns pages, and the caller synthesises.

## Configuration

`UHB_LLM_BASE_URL` (default local Ollama), `UHB_LLM_MODEL` (default `qwen3:8b`),
`UHB_LLM_TOKEN` if the endpoint wants one, `UHB_LLM_REASONING` default `none`
(compiling is extraction, not deliberation: 2 s instead of 30 s on `qwen3:8b`).
Tests use a fake LLM; `UHB_OLLAMA_E2E=1` enables the one real compile.
