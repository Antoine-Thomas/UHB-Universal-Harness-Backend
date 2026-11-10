# Intégration du widget « 9+ UHB » dans une interface

Ce document explique comment poser le bouton **9+ UHB** et sa popup dans une interface
existante (dashboard, console de harness, page locale), et comment le point de couleur se
connecte à un serveur UHB qui tourne à côté.

Le markup de référence est celui de `uhb ui --markup`. Ce fichier en contient une copie
générée depuis le code : si les deux divergent, **c'est le code qui a raison** —
`uhb ui --markup` sort toujours la version courante.

---

## 1. Ce que vous obtenez

Trois propriétés, tenues par construction :

1. **Aucun CSS de page.** Toutes les règles sont préfixées `#uhb-badge`, `#uhb-modal`,
   `#uhb-dialog` ou `.uhb-dot`. Coller le widget ne touche ni `body`, ni vos variables CSS,
   ni votre grille.
2. **Aucun build, aucune dépendance.** CSS et JS sont en ligne. Pas de CDN, pas de police
   téléchargée, pas de framework.
3. **Aucune origine en dur.** L'appel est `fetch(BASE + "/status")`, où `BASE` vient de
   l'attribut `data-uhb-base` du bouton. Le même markup marche donc sur la page servie par
   UHB (`BASE` vide = même origine) et dans un dashboard servi ailleurs.

## 2. Deux manières, une seule recommandée

| Manière | Ce que ça donne | Verdict |
| --- | --- | --- |
| **Coller le markup** (section 4) | le vrai bouton flottant + sa popup, dans votre layout | à faire |
| `<iframe src="http://127.0.0.1:8101/">` | la page d'état complète, dans un cadre | utile pour l'état, **pas** pour le bouton |

L'iframe ne convient pas au bouton : la popup est en `position: fixed; inset: 0`, donc
dimensionnée sur le viewport de l'iframe. Dans une iframe de la taille du badge (120×60),
la popup est rognée à 120×60 et devient illisible. Réservez l'iframe à la page d'état,
en grand :

```html
<iframe src="http://127.0.0.1:8101/" title="État UHB"
        style="width:560px;height:420px;border:1px solid #e2e7ec;border-radius:12px"></iframe>
```

## 3. Pas à pas

1. **Lancez UHB** sur la machine qui sert l'interface :
   `uhb run --mode dev --port 8101` — le mode affiché dans la popup est celui-là.
2. **Trouvez le layout** de votre interface : le fichier qui contient la balise de fin
   `</body>`, ou le composant racine si tout est généré en JS.
3. **Collez le markup** de la section 4 juste avant `</body>`.
4. **Ajustez `data-uhb-base`** si UHB n'écoute pas sur `127.0.0.1:8101` : c'est la seule
   valeur à changer, elle est lue au chargement.
5. **Rechargez la page.** Le bouton apparaît en bas à droite, avec un point vert ou
   orange. Cliquez : la popup s'ouvre, l'état se remplit.

## 4. Le markup complet

Généré par `uhb ui --markup`. Copiez tout ce bloc :

```html
<style>
  /* UHB widget — every rule is scoped; nothing leaks into the host page. */
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
  @media (max-width: 480px) { #uhb-dialog { padding: 18px 16px 14px; } }
</style>

<button id="uhb-badge" type="button" aria-haspopup="dialog" aria-controls="uhb-modal"
        data-uhb-base="http://127.0.0.1:8101">
  <span class="uhb-dot" id="uhb-dot"></span>9+ UHB
</button>

<div id="uhb-modal" hidden role="dialog" aria-modal="true" aria-labelledby="uhb-title">
  <div id="uhb-dialog">
    <button id="uhb-close" type="button" aria-label="Fermer">&times;</button>
    <h2 id="uhb-title">UHB — 0.1.0</h2>
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
</div>

<script>
(function () {
  var badge = document.getElementById("uhb-badge");
  if (!badge) { return; }
  var modal = document.getElementById("uhb-modal");
  var close = document.getElementById("uhb-close");
  var base = (badge.dataset.uhbBase || "").replace(/\/+$/, "");
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
</script>
```

## 5. Comment le point de couleur est décidé

Le widget appelle `GET <BASE>/status` au chargement et à chaque ouverture de la popup.
La réponse contient, entre autres :

```json
{
  "connected": true,
  "version": "0.1.0",
  "mode": "dev",
  "harness": "hermes",
  "providers": [
    {"name": "deepseek", "key_loaded": true, "local": false},
    {"name": "ollama", "key_loaded": false, "local": true}
  ],
  "modes": ["chat", "dev", "observe", "review"],
  "docs": {"manual": "/docs/manuel.pdf", "manual_en": "/docs/manual.pdf"}
}
```

Règle appliquée par le JS :

| Point | Condition |
| --- | --- |
| vert | au moins un fournisseur utilisable (`key_loaded` ou `local`) |
| orange | le serveur répond, mais aucune clé chargée : UHB tourne, aucun modèle distant n'est joignable |
| rouge | `fetch` échoue : serveur arrêté, mauvais port, ou requête bloquée par le navigateur |

`connected` vaut toujours `true` quand le serveur répond — la réponse est elle-même la
preuve de vie. La disponibilité des modèles est une **autre** information, portée par
`providers[]`.

### CORS : pourquoi ça marche depuis un autre port

Votre interface écoute sur un port, UHB sur un autre : pour le navigateur, ce sont deux
origines différentes, et un `fetch` sans en-tête CORS serait bloqué. UHB répond donc :

- `Access-Control-Allow-Origin` **égal à votre origine** quand elle est locale
  (`http://127.0.0.1:<port>`, `http://localhost:<port>`, `http://[::1]:<port>`) ou `null`
  (page ouverte depuis le disque) ;
- `OPTIONS` répond aux préflight avec `GET, OPTIONS` ;
- **jamais `*`** : un site quelconque que vous visitez n'obtient pas l'en-tête et ne peut
  pas lire la liste de vos fournisseurs.

Si votre interface est servie depuis une autre machine, le navigateur bloquera : c'est
voulu. Dans ce cas, proxifiez `/status` côté serveur d'interface.

## 6. Variante minimale : le point seul

Si vous ne voulez que la pastille, sans popup, ce bloc suffit (même contrat, même CORS) :

```html
<style>.uhb-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#8a949c}
.uhb-dot.uhb-ok{background:#1f9d55}.uhb-dot.uhb-warn{background:#d98324}.uhb-dot.uhb-bad{background:#c0392b}</style>
<span id="uhb-dot" class="uhb-dot"></span>
<script>
fetch("http://127.0.0.1:8101/status")
  .then(function (r) { return r.ok ? r.json() : null; })
  .then(function (d) {
    var ready = d && (d.providers || []).some(function (p) { return p.key_loaded || p.local; });
    var dot = document.getElementById("uhb-dot");
    dot.className = "uhb-dot " + (d === null ? "uhb-bad" : ready ? "uhb-ok" : "uhb-warn");
  })
  .catch(function () { document.getElementById("uhb-dot").className = "uhb-dot uhb-bad"; });
</script>
```

## 7. Dépannage

| Symptôme | Cause la plus probable |
| --- | --- |
| le bouton n'apparaît pas | markup posé après `</body>`, ou `display:none` hérité : cherchez `#uhb-badge` dans l'inspecteur |
| point rouge, popup « injoignable » | UHB n'écoute pas : `uhb run --mode dev --port 8101`, puis `curl <BASE>/status` |
| point rouge alors que le serveur tourne | port différent de `data-uhb-base`, ou requête bloquée : la console réseau nomme l'erreur CORS |
| point orange en permanence | aucune clé chargée : `uhb status` liste les fournisseurs et les variables attendues |
| « Manuel (PDF) » → 404 | paquet installé sans ses données : `pip install -e .` depuis un checkout, puis `uhb docs` |
| la popup est coupée | un conteneur `overflow: hidden` crée un contexte d'empilement : posez le widget à la racine du `body` |

## 8. Ce qu'il ne faut pas faire

- **Ne pas copier la page entière** (`GET /`) dans votre layout : son CSS de page
  (`body`, `main`, `dl`) écraserait le vôtre. Seul le bloc de la section 4 est sûr.
- **Ne pas mettre le widget dans une iframe de la taille du badge** : la popup est rognée.
- **Ne pas appeler `/status` depuis un serveur tiers sans proxy** : l'API est locale par
  conception, elle ne s'ouvre pas au réseau.
