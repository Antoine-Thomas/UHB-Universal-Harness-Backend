# hub — the reference brain of UHB

Three pieces, one contract: a **wiki** that compiles sources, **modes** that say
what a turn may do, and a **router** that says which model does it.

    uhb/hub/
    ├── wiki/      raw/ -> wiki/ -> schema.md, with ingest, query, lint
    ├── modes/     one JSON profile per mode: read/write scope, tools, tier
    ├── router/    classify -> plan a chain -> execute with fallback
    └── guard.py   the write gate: check every path, then write

Each sub-package has its own README. This one is about using them together.

## Modes and router, in one call

```python
from pathlib import Path
from uhb.hub.modes import get_mode
from uhb.hub.wiki import ingest

REPO = Path("/path/to/repo")          # holds uhb/
WIKI = REPO / "uhb" / "hub" / "wiki"  # holds raw/ and wiki/
dev = get_mode("dev")                 # read **, write uhb/tests/docs, no paid model

result = ingest("raw/notes/note.md", workspace=WIKI, mode=dev, project=REPO)
```

What happened, in order:

1. `mode=dev` — the envelope is resolved before anything else. An unknown mode
   raises before a model is called or a file is touched.
2. The compile call goes through `routed_llm(dev, complexity="standard")`: the
   router plans `eco → free` (paid is refused), and the first model that answers
   wins — DeepSeek flash when a key is configured, the local Ollama otherwise.
3. Every planned write (pages, `index.md`, `log.md`) is checked against the mode
   *before* the first byte lands. `chat` or `review` raise `ModeViolation` and
   leave the tree untouched — all-or-nothing, index included.

Swap the mode and nothing else changes:

| Mode | `ingest` outcome |
| --- | --- |
| `dev` | compiles, writes pages and catalogue |
| `chat` | `ModeViolation: mode 'chat' is read-only` |
| `review` | `ModeViolation: mode 'review' is read-only` |
| `observe` | `ModeViolation: may not write 'uhb/hub/wiki/wiki/concepts/hub.md'` — its scope is logs and reports |

## Where the model is chosen

| Step | Function | Cost |
| --- | --- | --- |
| classify | `router.classify(prompt)` | ~7 µs, no model |
| plan | `router.route(prompt, mode)` | ~13 µs, no model |
| ask | `mode.system_prompt` + the routed call | one completion |
| gate | `guard.commit(mode, planned, project=...)` | one glob match per file |
| write | `atomic_write` per file, after every check | disk |

The entrypoints follow the same rule: `uhb execute --mode dev "..."`, the
`claude`-compatible binary with `--mode`, and the HTTP server with a `mode` field
in the payload all route when the caller names no model, and refuse an unknown
mode at the boundary (400 / exit 1 / error event).

## The two invariants

- **Policy is data, and it is closed.** A mode is a JSON file; an unknown key is
  a load error naming the key. The routing policy is `catalog.json`, validated
  against the provider table: a route pointing at a model nobody serves fails at
  load, not at 3 a.m.
- **Check before the act.** Nothing in `uhb/hub` writes a file without passing
  `guard.commit`. A mode with no `write` glob is read-only, not "unrestricted by
  accident", and a refusal leaves the previous bytes exactly as they were.

## Not wired yet

Nothing in `uhb/core` or `uhb/hub` writes a file without a mode any more: the
wiki (`ingest`, `lint`, `write_index`) and the harness memory (`MemorySync`) all
raise `ModeRequired` when the envelope is missing. What is *not* covered is a
turn executed by a real harness: once `hermes` runs as a subprocess, its own
writes are outside UHB's reach by construction.
