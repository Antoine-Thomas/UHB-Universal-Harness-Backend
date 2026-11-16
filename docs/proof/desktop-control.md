# Desktop control, proved on the Wikipedia case

What was asked: *the harness must reach the structure of Windows, macOS and Linux
to drive any component — the simplest one being the browser: open it, find
Wikipedia, show a wiki in the window.*

What this document proves, on the reference machine (Windows 11, Chrome as the
default browser), with no browser automation driver and no Playwright:

1. the browser is opened through the operating system;
2. its window is found in the accessibility tree;
3. the page content is readable through that tree — real article sections, real
   links, real buttons;
4. a click on a referenced element lands.

## The run

```
backend : {'platform': 'windows', 'backend': 'uiautomation', 'available': True, 'reason': None}
open_url: True https://fr.wikipedia.org/wiki/Intelligence_artificielle
fenêtre : Sans titre - Google Chrome
snapshot : True éléments : 32
  c5f14b3e:0 | WindowControl      | Sans titre - Google Chrome
  c5f14b3e:1 | PaneControl        | Intelligence artificielle — Wikipédia - Google Chrome
  c5f14b3e:2 | ButtonControl      | Réduire
  c5f14b3e:3 | ButtonControl      | Agrandir
  c5f14b3e:5 | ButtonControl      | Fermer
  c5f14b3e:7 | ButtonControl      | Retour
click premier élément : True click
```

Second read, with the node budget at its default (250) — the page itself, not only
the window chrome:

```
ok: True count: 101
contenu de page trouvé: 7
  'Intelligence artificielle — Wikipédia'
  'Intelligence artificielle généraleAfficher / masquer la sous-section Intelligence artificielle générale'
rôles: ButtonControl 53, TextControl 12, ListItemControl 10, HyperlinkControl 10, GroupControl 4
```

That is the article's own section header and ten hyperlinks: the tree is the page,
not a screenshot of it. `desktop_find` resolves them, `desktop_click` activates
them by reference.

## Two lessons taken from `vercel-labs/agent-desktop`

* **snapshot → ref → action**, and a ref dies with its snapshot. The first attempt
  above shows why: the window was titled *Sans titre* when it was listed, and
  *Intelligence artificielle — Wikipédia* a second later. Acting on the first name
  would have targeted a window that no longer existed. `stale_ref` is a refusal,
  never a best guess — the attempt returned `target_not_found`, which is the
  correct answer for a title that moved.
* **observation and action are different tools.** `desktop_windows`,
  `desktop_snapshot` and `desktop_find` read; `desktop_click`, `desktop_type` and
  `desktop_open_url` act. Reading the tree grants nothing, exactly like
  `GET /tools` versus `POST /tools/execute`.

## What is not done, and is not pretended

* **macOS and Linux are not wired.** `backend_status()` returns
  `platform_unavailable` with the reason (AX grant on macOS, `at-spi2-core` on
  Linux). A structured refusal, not an exception and not a lie.
* **Windows needs an optional extra**: `pip install "uhb[desktop]"` (UI Automation
  through `uiautomation`). Absent, every desktop tool answers `backend_missing`.
* **No `file:` and no `javascript:`**: `desktop_open_url` accepts http(s) only.
  A tool that opens local files is a tool that reads them.
* **A desktop snapshot contains private content** — window titles, page text, field
  values. It is returned to the caller and written to no log.
* The node budget is spent depth-first: a window's own chrome is read before the
  page inside it, which is why 32 elements show the title bar and 101 show the
  article. Measured, not assumed.

## Reproducing

```bash
cd <checkout>
pip install "uhb[desktop]"                     # Windows only, optional
PYTHONPATH=<checkout> python <script>          # the run above
# or, through the server, under the mode that declares it:
uhb run --mode dev --port 8101
curl -s -X POST 127.0.0.1:8101/tools/desktop_windows -H 'Content-Type: application/json' -d '{}'
```
