"""Generate ``test_ui.html`` from the real widget markup.

The point of the page is to prove the shipped widget, not a copy of it: the
snippet comes from ``uhb.entrypoints.panel.snippet()`` — the same call behind
``uhb ui --markup`` — so a divergence in the code shows up in the file.

    python tools/make_test_ui.py            # rewrite test_ui.html
    python tools/make_test_ui.py --check    # exit 1 when the file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "test_ui.html"

TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UHB — page de test du widget</title>
<style>
  /* Host page styles, deliberately distinctive: if the widget leaked a rule,
     this page would show it. Nothing below targets a UHB selector. */
  body {{ margin: 0; padding: 40px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         background: #f6f8fa; color: #10151a; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  p {{ max-width: 62ch; line-height: 1.55; color: #3b4753; }}
  code {{ background: #eef2f6; padding: 1px 5px; border-radius: 4px; }}
  .note {{ margin-top: 28px; padding: 14px 16px; background: #fff; border: 1px solid #e2e7ec;
           border-radius: 10px; max-width: 62ch; font-size: 14px; }}
</style>
</head>
<body>
<h1>Page de test — widget « 9+ UHB »</h1>
<p>Cette page ne contient aucun style UHB : le bloc ci-dessous est le markup exact de
<code>uhb ui --markup</code>, collé avant <code>&lt;/body&gt;</code>.
Le point du bouton interroge <code>GET /status</code> au chargement.</p>

<div class="note">
  <strong>Lecture du point</strong><br>
  vert <code>#1f9d55</code> = serveur joignable et au moins un fournisseur utilisable ·
  orange <code>#d98324</code> = serveur joignable, aucune clé chargée ·
  rouge <code>#c0392b</code> = serveur injoignable.<br>
  Ajouter <code>?open=1</code> à l'URL clique le bouton au chargement, pour la capture
  de la popup ouverte.
</div>

{markup}
<script>
// Opt-in auto-open, so a screenshot can show the popup without a human click.
// It calls the widget's own click handler: the badge is a real button.
if (new URLSearchParams(location.search).get("open") === "1") {{
  document.getElementById("uhb-badge").click();
}}
</script>
</body>
</html>
"""


def render() -> str:
    # Running as a script puts tools/ on sys.path, not the repository root.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from uhb.entrypoints import panel

    markup = panel.snippet(version=__import__("uhb").__version__, base="http://127.0.0.1:8101")
    return TEMPLATE.format(markup=markup)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when test_ui.html is stale")
    args = parser.parse_args(argv)

    wanted = render().replace("\r\n", "\n")
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if current.replace("\r\n", "\n") != wanted:
            print("test_ui.html is stale: run python tools/make_test_ui.py", file=sys.stderr)
            return 1
        print("test_ui.html matches uhb ui --markup")
        return 0

    TARGET.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({len(wanted)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
