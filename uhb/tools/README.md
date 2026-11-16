# `uhb/tools/` — the tools a turn may call

A tool here is a plain function with a typed result: no agent loop, no prompt, no
model. The permission check is the same one every other act goes through — the
mode's `tools` list — so `GET /modes` already tells a client what is available
before it asks for it.

| Tool | Function | Gated by |
| --- | --- | --- |
| `execute_code` | `uhb.tools.execute_code` | `"execute_code"` in the mode's `tools` |

Shipped modes: `dev` carries the tool. `chat`, `review` and `observe` do not, and
that is a decision, not an oversight — executing is a write to the world.
Declaring it in another profile is one JSON edit, no code:

```json
"tools": ["read_file", "search", "execute_code"]
```

## `execute_code` — the missing step between "here is the code" and "here is the chart"

Asked for a graph, a model writes matplotlib code and stops. The tool writes that
code to a file, runs it in its own directory, and reports three things: the streams,
the exit code, and **the images the snippet produced** — path, MIME, size, and
optionally a `data:` URL a UI can put straight into `<img src>`.

```python
from uhb.tools import execute_code

result = execute_code("print(2 + 2)", mode="dev")
result.stdout                 # '4\n'
result.ok                     # True

chart = execute_code(open("chart.py").read(), mode="dev", include_base64=True)
chart.images[0].path          # ~/.uhb/runs/run-…/sine.png
chart.images[0].data_url      # 'data:image/png;base64,iVBORw0…'
```

### The result, as JSON

```json
{
  "ok": true,
  "exit_code": 0,
  "stdout": "saved sine.png\n",
  "stderr": "",
  "timed_out": false,
  "duration_s": 1.188,
  "timeout_s": 30.0,
  "mode": "dev",
  "run_dir": "~/.uhb/runs/run-20261116-000930-412f11",
  "command": ["…/python.exe", "-I", "-B", "-X", "utf8", "…/_uhb_runner.py"],
  "images": [
    {"path": "~/.uhb/runs/run-20261116-000930-412f11/sine.png",
     "relative": "sine.png", "mime": "image/png", "bytes": 39464,
     "data_url": "data:image/png;base64,iVBORw0…"}
  ],
  "notes": []
}
```

`notes` is not decoration: every caveat the tool can have lands there — a truncated
stream with the number of dropped characters, an image too large to inline, an
image over the size cap that was not reported at all. A silent truncation would
read as "the code printed that", and a silent omission as "there was no image".

### Images are discovered, not declared

The tool snapshots the run directory before and after, and reports every file with
an image suffix (`.png .jpg .jpeg .gif .webp .svg .bmp .tif .tiff`) that was created
or rewritten. Consequences worth knowing:

- `savefig("sine.png")` works, and so does `savefig("figs/sine.png")` — subdirectories
  are walked;
- a file that was already there and untouched is **not** reported again, so a reused
  working directory does not resurrect old charts;
- the harness's own files (`code.py`, `_uhb_runner.py`, `.mpl/`) are never artefacts;
- an image written **outside** the run directory is invisible to the tool. Write
  relative paths, or point `workdir` at the tree you mean. The snippet starts with
  its working directory set to the run directory for exactly that reason.

### What is guaranteed

| Guarantee | How | Test |
| --- | --- | --- |
| nothing runs without a mode that lists the tool | `ModeRequired` / `ModeViolation` before the directory is even created | `test_a_read_only_mode_is_refused_before_anything_runs` |
| the operator's keys stay out of the child | allow-listed env (`PATH`, `SYSTEMROOT`, temp dirs…) + run dir as `HOME` | `test_the_child_cannot_see_the_operator_keys` |
| `savefig` works with no display | `MPLBACKEND=Agg`, `MPLCONFIGDIR` inside the run | `test_the_child_gets_a_headless_matplotlib_and_a_private_home` |
| a runaway snippet dies | `timeout` on `communicate`, then the whole process tree is killed | `test_a_timeout_kills_the_process_tree` |
| output is bounded and honest | `max_output` per stream, with the dropped count in `notes` | `test_stdout_is_truncated_with_an_explicit_note` |
| the JSON route cannot escalate | the server executes only as its own `--mode` | `test_the_server_refuses_to_lend_another_mode` |

### What is NOT guaranteed

This is **containment, not a security boundary**. The snippet runs as your user,
with your filesystem and your network, on purpose: the ordinary use is "plot my
data" or "check this regex". It is not a sandbox for hostile code.

- POSIX adds `RLIMIT_CPU` (timeout + 1 s), `RLIMIT_AS` (2 GiB), `RLIMIT_FSIZE`
  (64 MiB) and `RLIMIT_NOFILE` (256), set *inside* the child — `preexec_fn` is
  unsafe in a threaded server.
- **Windows has none of those limits**; only the timeout applies.
- The network is not sealed. If the snippet must not reach out, run it on a machine
  that cannot.

Do not publish `POST /tools/execute` beyond loopback; `uhb run` binds `127.0.0.1`
for that reason.

### Limits, as constants

| Constant | Default | Meaning |
| --- | --- | --- |
| `DEFAULT_TIMEOUT` | 30 s | what a call gets when it does not ask |
| `MAX_TIMEOUT` | 300 s | a longer run is a batch job, not a tool call |
| `MIN_TIMEOUT` | 0.5 s | below this, the interpreter start-up is the measurement |
| `MAX_OUTPUT` | 20 000 chars | per stream, with an explicit truncation marker |
| `MAX_CODE_CHARS` | 200 000 | a snippet, not a program |
| `MAX_INLINE_BYTES` | 1.5 MB | above this, the image is described, not inlined |
| `MAX_IMAGE_BYTES` | 32 MB | above this, the image is not reported at all |
| `MAX_IMAGES` | 8 | the rest are listed in `notes` |

`UHB_RUNS_DIR` moves the run directories (default `~/.uhb/runs`). They are **kept**:
the paths have to outlive the call for a UI to read the image. Deleting
`~/.uhb/runs/*` is safe and never touches anything else.

### Wiring it to an interface

`POST /tools/execute` on the running server:

```bash
curl -s -X POST http://127.0.0.1:8101/tools/execute \
  -H 'content-type: application/json' \
  -d '{"code": "print(2+2)", "include_base64": true}'
```

```js
// The interface posts the code the model wrote, then shows what came back.
const reply = await fetch("http://127.0.0.1:8101/tools/execute", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ code, include_base64: true }),
}).then((r) => r.json());

for (const image of reply.images) {
  const img = new Image();
  // An http page cannot load a file:// image: inline it, or read the path from a
  // local process. `path` is for the shell, `data_url` is for the browser.
  img.src = image.data_url;
  document.body.append(img);
}
```

Status codes: `200` ran (read `ok`), `400` the call is malformed (empty code, bad
timeout), `403` the mode does not allow it — including a body that names a mode
other than the server's own, which is refused rather than honoured.

## Tests

```bash
python -m pytest uhb/tests/test_tools_execute.py -q   # ~3 s, no network, no matplotlib
```

The suite spawns real processes and writes real files: the snippets use the standard
library only, and the "image" is a 1×1 PNG stored as bytes, so the contract is pinned
without dragging a plotting stack into CI.
