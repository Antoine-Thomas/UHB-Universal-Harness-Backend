"""The UHB widget — one self-contained badge, its modal, and no external asset.

Two artefacts come out of this module:

- :data:`HTML` — the full page served at ``GET /``: the badge plus a small status
  view. Useful on its own, and the place where the widget is exercised.
- :func:`snippet` — **only** the badge, the modal, their scoped CSS and their JS,
  ready to paste into someone else's dashboard. ``uhb ui --markup`` prints it.

Three rules the snippet respects, because a widget that breaks its host is worse
than no widget:

1. **No page-level CSS.** Nothing here touches ``body``, ``main``, ``h1`` or ``dl``.
   Every rule is scoped to ``#uhb-badge`` / ``#uhb-modal`` / ``#uhb-dialog``, and the
   colour variables are declared *on the widget itself*, so the host's own variables
   are neither read nor overwritten.
2. **No hard-coded origin.** The page calls ``fetch("/status")``; the snippet calls
   ``fetch(BASE + "/status")`` where ``BASE`` comes from ``data-uhb-base`` on the
   badge. The same markup works same-origin (the panel) and cross-origin (a
   dashboard on another port), and the download links follow the same base.
3. **No build step.** Inline CSS, inline JS, no CDN, no font download, no tracking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The manual ships next to the package: `uhb/docs/`. Resolved from the module,
#: never from the current directory, so it works from any working tree.
DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"

BADGE_LABEL = "9+ UHB"

#: Page-only rules. Never exported: they would reset a host layout.
PAGE_CSS = """  * { box-sizing: border-box; }
  body { margin: 0; background: #fbfcfd; color: #10151a;
         font: 15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }
  main { max-width: 720px; margin: 48px auto; padding: 0 20px; }
  h1 { font-size: 22px; margin: 0 0 4px; color: #0b3d5c; letter-spacing: .2px; }
  p.sub { margin: 0 0 28px; color: #5b6772; }
  dl { display: grid; grid-template-columns: 130px 1fr; gap: 8px 16px; margin: 0; }
  dt { color: #5b6772; } dd { margin: 0; font-variant-numeric: tabular-nums; }
  code { background: #eef2f5; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
  @media (max-width: 480px) {
    main { margin: 28px auto; } dl { grid-template-columns: 1fr; gap: 2px 0; }
    dt { margin-top: 10px; } }"""

#: The widget's whole visual contract, scoped on purpose: pasted into any page it
#: looks identical and cannot move the host's own styles.
WIDGET_CSS = """  /* UHB widget — every rule is scoped; nothing leaks into the host page. */
  .uhb-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
             background: #8a949c; margin-right: 6px; vertical-align: middle; }
  .uhb-dot.uhb-ok { background: #1f9d55; }
  .uhb-dot.uhb-warn { background: #d98324; }
  .uhb-dot.uhb-bad { background: #c0392b; }

  #uhb-badge, #uhb-modal { --uhb-ink: #10151a; --uhb-muted: #5b6772; --uhb-line: #e2e7ec;
    --uhb-brand: #0b3d5c; --uhb-ok: #1f9d55; --uhb-warn: #d98324; --uhb-bad: #c0392b; }

  #uhb-badge { position: fixed; right: 18px; bottom: 18px; z-index: 40;
    display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
    background: #fff; color: var(--uhb-ink); border: 1px solid var(--uhb-line);
    border-radius: 999px; padding: 8px 14px; font-size: 13px; font-weight: 600;
    box-shadow: 0 2px 10px rgba(16,21,26,.10); font-family: inherit; }
  #uhb-badge:hover { border-color: #c9d3db; }
  #uhb-badge:focus-visible { outline: 2px solid var(--uhb-brand); outline-offset: 2px; }

  #uhb-modal[hidden] { display: none; }
  #uhb-modal { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center;
    background: rgba(16,21,26,.45); padding: 16px; }
  #uhb-dialog { background: #fff; border-radius: 12px; width: 100%; max-width: 560px;
    max-height: 86vh; overflow: auto; padding: 22px 24px 18px; color: var(--uhb-ink);
    box-shadow: 0 18px 50px rgba(16,21,26,.28);
    font: 15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }
  #uhb-dialog h2 { margin: 0 0 2px; font-size: 18px; color: var(--uhb-brand); }
  #uhb-dialog .uhb-hint { margin: 0 0 16px; color: var(--uhb-muted); font-size: 13px; }
  #uhb-close { float: right; border: 0; background: none; font-size: 22px; line-height: 1;
    cursor: pointer; color: var(--uhb-muted); padding: 2px 6px; }
  #uhb-dialog ul.uhb-cmds { list-style: none; margin: 0 0 18px; padding: 0; }
  #uhb-dialog ul.uhb-cmds li { display: grid; grid-template-columns: 1fr; gap: 2px;
    padding: 7px 0; border-bottom: 1px solid var(--uhb-line); }
  #uhb-dialog ul.uhb-cmds code { background: none; padding: 0; font-size: 13px;
    color: var(--uhb-brand); }
  #uhb-dialog ul.uhb-cmds span { color: var(--uhb-muted); font-size: 13px; }
  #uhb-dialog .uhb-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 6px; }
  #uhb-dialog a.uhb-btn { display: inline-flex; align-items: center; gap: 8px;
    border: 1px solid var(--uhb-brand); background: var(--uhb-brand); color: #fff;
    border-radius: 8px; padding: 9px 14px; font-size: 14px; font-weight: 600;
    text-decoration: none; cursor: pointer; font-family: inherit; }
  #uhb-dialog a.uhb-btn.uhb-secondary { background: #fff; color: var(--uhb-brand); }
  @media (max-width: 480px) { #uhb-dialog { padding: 18px 16px 14px; } }"""

#: The two elements. `data-uhb-base` is the only thing a host has to set.
WIDGET_HTML = """<button id="uhb-badge" type="button" aria-haspopup="dialog" aria-controls="uhb-modal"
        data-uhb-base="__BASE__">
  <span class="uhb-dot" id="uhb-dot"></span>__BADGE__
</button>

<div id="uhb-modal" hidden role="dialog" aria-modal="true" aria-labelledby="uhb-title">
  <div id="uhb-dialog">
    <button id="uhb-close" type="button" aria-label="Fermer">&times;</button>
    <h2 id="uhb-title">UHB — __VERSION__</h2>
    <p class="uhb-hint" id="uhb-state">…</p>

    <ul class="uhb-cmds">
      <li><code>uhb status</code><span>version, fournisseurs, politique, modes</span></li>
      <li><code>uhb run --mode dev --port 8080</code><span>démarre ce serveur</span></li>
      <li><code>uhb modes list</code><span>ce que chaque mode a le droit d'écrire</span></li>
      <li><code>uhb config set routing.default deepseek/deepseek-flash</code><span>change le modèle</span></li>
      <li><code>uhb brain status</code><span>RAG, SiYuan, MARI : en ligne ou non</span></li>
      <li><code>uhb docs</code><span>chemin du manuel et URL</span></li>
    </ul>

    <div class="uhb-actions">
      <a class="uhb-btn" data-uhb-path="/docs/manuel.pdf" href="/docs/manuel.pdf" download>Manuel d'utilisation (PDF)</a>
      <a class="uhb-btn uhb-secondary" data-uhb-path="/docs/manual.pdf" href="/docs/manual.pdf" download>User manual (EN)</a>
      <a class="uhb-btn uhb-secondary" data-uhb-path="/status" href="/status">État JSON</a>
    </div>
  </div>
</div>"""

WIDGET_JS = """<script>
(function () {
  var badge = document.getElementById("uhb-badge");
  if (!badge) { return; }
  var modal = document.getElementById("uhb-modal");
  var close = document.getElementById("uhb-close");
  var base = (badge.dataset.uhbBase || "").replace(/\\/+$/, "");
  var lastFocus = null;

  // Download and JSON links follow the same base as the fetch: relative when the
  // widget is served by UHB, absolute when it is pasted into a dashboard elsewhere.
  document.querySelectorAll("[data-uhb-path]").forEach(function (link) {
    link.setAttribute("href", base + link.dataset.uhbPath);
  });

  function paint(data) {
    var providers = (data && data.providers) || [];
    var ready = providers.some(function (p) { return p.key_loaded || p.local; });
    var dot = document.getElementById("uhb-dot");
    dot.className = "uhb-dot " + (data === null ? "uhb-bad" : ready ? "uhb-ok" : "uhb-warn");
    document.getElementById("uhb-state").textContent = data === null
      ? "UHB injoignable sur " + (base || location.origin)
      : "Mode " + (data.mode || "?") + " · harness " + (data.harness || "?") +
        " · " + providers.length + " fournisseur(s), " +
        (ready ? "au moins un utilisable" : "aucune clé chargée");
  }

  function refresh() {
    fetch(base + "/status", { headers: { "accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(paint)
      .catch(function () { paint(null); });
  }

  function open() {
    lastFocus = document.activeElement;
    modal.hidden = false;
    close.focus();
    refresh();
  }
  function shut() {
    modal.hidden = true;
    if (lastFocus) { lastFocus.focus(); }
  }

  badge.addEventListener("click", open);
  close.addEventListener("click", shut);
  modal.addEventListener("click", function (event) { if (event.target === modal) { shut(); } });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) { shut(); }
  });

  refresh();
})();
</script>"""

#: Page-only script: fills the status table. Never part of the snippet.
PAGE_JS = """<script>
(function () {
  fetch("/status", { headers: { "accept": "application/json" } })
    .then(function (response) { return response.ok ? response.json() : null; })
    .then(function (data) {
      if (!data) { return; }
      var providers = data.providers || [];
      var ready = providers.some(function (p) { return p.key_loaded || p.local; });
      document.getElementById("s-dot").className = "uhb-dot " + (ready ? "uhb-ok" : "uhb-bad");
      document.getElementById("s-state").textContent =
        "en ligne · " + (ready ? "au moins un fournisseur utilisable" : "aucune clé chargée");
      document.getElementById("s-mode").textContent = data.mode || "—";
      document.getElementById("s-harness").textContent = data.harness || "—";
      document.getElementById("s-providers").textContent = providers.map(function (p) {
        return p.name + (p.local ? " (local)" : p.key_loaded ? " ✓" : " —");
      }).join(" · ");
      document.getElementById("s-modes").textContent = (data.modes || []).join(" · ");
    })
    .catch(function () {});
})();
</script>"""

HTML = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UHB — __VERSION__</title>
<style>
__PAGE_CSS__

__WIDGET_CSS__
</style>
</head>
<body>
<main>
  <h1>UHB <span style="font-weight:400;color:#5b6772">__VERSION__</span></h1>
  <p class="sub">Universal Harness Backend — état de cette instance.</p>
  <dl>
    <dt>Serveur</dt><dd><span class="uhb-dot" id="s-dot"></span><span id="s-state">…</span></dd>
    <dt>Mode actif</dt><dd id="s-mode">__MODE__</dd>
    <dt>Harness</dt><dd id="s-harness">__HARNESS__</dd>
    <dt>Fournisseurs</dt><dd id="s-providers">…</dd>
    <dt>Modes</dt><dd id="s-modes">…</dd>
    <dt>Manuel</dt><dd><a href="/docs/manuel.pdf" download>Français (PDF)</a> ·
      <a href="/docs/manual.pdf" download>English (PDF)</a></dd>
  </dl>
</main>

__WIDGET_HTML__

__PAGE_JS__

__WIDGET_JS__
</body>
</html>
"""


def _fill(text: str, *, version: str, base: str = "", mode: str = "", harness: str = "") -> str:
    # The blocks are substituted FIRST: the widget markup carries its own
    # __BADGE__ / __BASE__ / __VERSION__ tokens, which a second pass would miss.
    composed = (
        text.replace("__PAGE_CSS__", PAGE_CSS)
        .replace("__WIDGET_CSS__", WIDGET_CSS)
        .replace("__WIDGET_HTML__", WIDGET_HTML)
        .replace("__WIDGET_JS__", WIDGET_JS)
        .replace("__PAGE_JS__", PAGE_JS)
    )
    return (
        composed.replace("__VERSION__", version)
        .replace("__BASE__", base)
        .replace("__MODE__", mode or "—")
        .replace("__HARNESS__", harness or "—")
        .replace("__BADGE__", BADGE_LABEL)
    )


def render(*, version: str, mode: str, harness: str, base: str = "") -> str:
    """The full page. ``base`` stays empty: the page is served by UHB itself."""
    return _fill(HTML, version=version, base=base, mode=mode, harness=harness)


def snippet(*, version: str, base: str = "http://127.0.0.1:8101") -> str:
    """The paste-ready widget: scoped CSS, badge, modal, JS — nothing page-level.

    ``base`` lands in ``data-uhb-base``; the host can change it in place without
    touching the JS.
    """
    return (
        "<style>\n" + WIDGET_CSS + "\n</style>\n\n"
        + _fill(WIDGET_HTML, version=version, base=base)
        + "\n\n"
        + WIDGET_JS
    )


def status_payload(
    *,
    version: str,
    mode: str,
    harness: str,
    providers: list[dict[str, Any]],
    modes: list[str],
) -> dict[str, Any]:
    """``GET /status``: what the widget and any other control plane needs.

    ``connected`` means this server answered — the response itself is the proof.
    Whether a model can actually be called is a separate fact, carried by
    ``providers[].key_loaded`` and ``providers[].local``.
    """
    return {
        "connected": True,
        "version": version,
        "mode": mode,
        "harness": harness,
        "providers": providers,
        "modes": modes,
        "docs": {"manual": "/docs/manuel.pdf", "manual_en": "/docs/manual.pdf",
                 "status": "/status", "panel": "/"},
        "endpoints": {
            "openai": "/v1/chat/completions",
            "anthropic": "/v1/messages",
            "models": "/v1/models",
            "health": "/health",
        },
    }


def banner(*, version: str, mode: str, host: str, port: int, providers_ready: int, providers_total: int) -> str:
    """The lines ``uhb run`` prints before it blocks. No credential, no noise."""
    base = f"http://{host}:{port}"
    return "\n".join(
        [
            f"  UHB {version} · Universal Harness Backend",
            f"  mode      {mode}",
            f"  providers {providers_ready}/{providers_total} utilisables",
            f"  panel     {base}/",
            f"  manuel    {base}/docs/manuel.pdf",
        ]
    )


def manual_path(lang: str = "fr") -> Path:
    """Absolute path of an embedded manual. Never resolves through the cwd."""
    name = "MANUEL_UHB_EN.pdf" if lang.lower().startswith("en") else "MANUEL_UHB.pdf"
    return DOCS_DIR / name
