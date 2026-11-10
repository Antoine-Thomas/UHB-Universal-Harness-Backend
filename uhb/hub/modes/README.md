# hub/modes — what an agent may do, as data

A mode is the envelope of a turn: read scope, write scope, tools, system prompt,
model tier, turn budget. It is JSON, not code, so adding a mode is adding a file.

## The four shipped modes

| Mode | Reads | Writes | Tools | Tier |
| --- | --- | --- | --- | --- |
| `chat` | `wiki/**`, `raw/**` | nothing | read, search | eco |
| `dev` | everything | `uhb/**`, `tests/**`, `docs/**`, harness memory files | read, write, patch, search, git, run_tests | eco |
| `review` | everything | nothing | read, search, git_diff | eco |
| `observe` | `wiki/**`, `logs/**`, `data/**`, `*.log` | `wiki/log.md`, `reports/**` | read, search, probe | free |

All four ship with `allow_paid: false`: a paid model is a manual decision, never
a default the router can reach on its own.

## Adding a mode

Drop `audit.json` in `profiles/` (or in the directory named by `UHB_MODES_DIR`,
which overlays the shipped ones):

```json
{
  "name": "audit",
  "description": "Inventory a tree, never edit it.",
  "kind": "execution",
  "read": ["**"],
  "write": ["reports/**"],
  "deny": ["**/.env", "**/*.key"],
  "tools": ["read_file", "search"],
  "system_prompt": "You inventory, you do not change.",
  "constraints": ["Every count comes with the command that produced it."],
  "model": {"tier": "eco", "temperature": 0.0, "max_tokens": 4096},
  "allow_paid": false,
  "max_turns": 3
}
```

## Fail-closed rules

- an unknown key is an error — a typo must not silently widen a permission
- `deny` wins over `read` and `write`; the default for both is *nothing*
- no tool is usable unless it is listed
- absolute patterns and `..` are refused at load time
- `check_write()` raises before anything is written, not after
- the registry is read once: a running process does not gain permissions because
  a file changed. Reload deliberately with `load_modes(refresh=True)` or
  `clear_cache()`.
