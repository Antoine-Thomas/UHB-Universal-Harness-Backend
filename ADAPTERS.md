# ADAPTERS — contrat par control plane

Un adapter par control plane connu : ce que le produit offre réellement, par quelle
porte un backend externe entre, ce que l'adapter doit fournir, l'effort et le
risque.

Chaque fiche cite les preuves mesurées en Phase 0 (fichiers et docs lus dans les dépôts publics) ; une affirmation sans preuve est marquée **[À VÉRIFIER]**.

---

## 1. Harness Maxxing

**Dépôt** — https://github.com/makushingu/harness-maxxing (Fair Source,
source-available, **pas** OSI).

### Contrat fourni

**Aucun contrat d'exécution natif.** L'API HTTP documentée (`docs/dev/API.md`,
v2.0) est une API de *control plane* : `/api/sessions`, `/api/sessions/{id}/stream`,
`/api/crons`, `/api/skills`, `/api/claude/settings`. Aucun endpoint ne déclare un
backend d'exécution, et le produit exige un abonnement Claude ou une clé Anthropic.

### Voie d'intégration

**a) Fake CLI — la porte solide.** `server/maxxing/sessions/spawn.py` :

```python
def claude_bin() -> str:
    explicit = os.environ.get("MAXXING_CLAUDE_BIN")
    if explicit:
        return explicit
    return shutil.which("claude", path=base_env()["PATH"]) or "claude"
```

`MAXXING_CLAUDE_BIN` est documenté (`docs/dev/INTERNALS.md`, `CONTRIBUTING.md`) et
utilisé par la suite de tests du produit (`server/tests/fake_claude.py`, 541
lignes). Contrat de substitution :

- **argv** : `-p --output-format stream-json --verbose`, puis `--model <id>`,
  `--permission-mode <mode>`, `--mcp-config <fichier> --permission-prompt-tool
  <outil>`, et `--session-id <csid>` (fil neuf) ou `--resume <csid>`.
- **entrée** : `--input-format stream-json` ; le prompt arrive sur stdin en message
  utilisateur JSONL, stdin laissé ouvert (permet un *steer* en cours de tour).
- **sortie** : JSONL sur stdout, `type` ∈ `assistant`, `user`, `result`, `system`
  (`subtype: init|task_started|…`), `stream_event`, `control_response`.
- **contrôle** : `{"type":"control_request","request":{"subtype":"interrupt"}}` sur
  stdin interrompt le tour.
- **filtrage** : les lignes à `parent_tool_use_id` non vide (sous-agents) sont
  écartées de la normalisation (`server/maxxing/sessions/normalize.py`).
- **sessions** : `stream-json` de Claude Code, relu par SSE interne
  `GET /api/sessions/{id}/stream?from=` (`event: events|state|reset`).

**b) Proxy Anthropic.** Le produit lance le CLI officiel, qui respecte
`ANTHROPIC_BASE_URL`. `base_env()` copie `os.environ`, donc une variable posée dans
l'unité systemd du serveur atteint le processus `claude`. Réserve : cette variable
est dans `_RESERVED` — un secret de vault marqué `expose` ne peut pas la poser, et
la doc utilisateur ne présente pas cette voie comme un hook. **[hypothèse]** elle
fonctionne, mais elle n'est pas un contrat.

### Ce que l'adaptateur doit fournir

Un binaire qui parle le contrat ci-dessus (`execute_task` ≈ une invocation par tour,
`cancel` ≈ `control_request interrupt`, `health` ≈ sortie immédiate sans appel
modèle), ou un endpoint Anthropic-compatible sur `127.0.0.1:PORT` avec
`ANTHROPIC_BASE_URL` posé dans l'environnement du service.

### Effort — 1 à 3 semaines

Le fake CLI est le chemin court : le format est déjà celui de Claude Code, donc un
adapter qui émet du `stream-json` valide fonctionne en quelques jours ; le reste est
sessions, MCP et streaming d'outils.

### Risque — élevé

Interface non contractuelle : les flags sont construits dans `runner.py`, pas dans
une doc d'extension, et changent avec les versions (notes par build,
`docs/upgrade/2.0.0a1xx.md`). Licence Fair Source : redistribuer un fork modifié
est permis, l'utiliser dans une offre payante ne l'est pas sans accord. Un adapter
branché par variable d'environnement ne copie aucun code du produit.

**Verdict — un backend externe peut-il s'y plugger sans patcher le code ?**
**Partiellement.** Oui par `MAXXING_CLAUDE_BIN` ou `ANTHROPIC_BASE_URL`, non par un
contrat d'exécution documenté.

---

## 2. OpenClaw

**Dépôt** — https://github.com/openclaw/openclaw (MIT, OpenClaw Foundation).

### Contrat fourni

Deux surfaces d'exécution documentées et distinctes.

**a) Agent harness plugin** — `docs/plugins/sdk-agent-harness*` (9 pages). Import
`openclaw/plugin-sdk/agent-harness`. Forme obligatoire :

```ts
const harness: AgentHarnessV2 = {
  id: "my-harness",
  label: "My native agent harness",
  supports(ctx) { /* { supported, priority } ou { supported:false, reason } */ },
  async runAttempt(params) { /* exécute le tour préparé */ },
};
api.registerAgentHarness(harness);
```

- **Obligatoire** : `id`, `label`, `supports`, `runAttempt`.
- **Optionnel** : `runIsolatedCompletionV2`, `delegatedExecutionPluginIds`,
  `authBootstrap: "harness"`, `workspaceEnvironment`.
- **Le core prépare** : provider et modèle, auth, budget de contexte, transcript,
  workspace, politique d'outils, callbacks. Le harness reçoit `params.prompt`,
  `params.tools`, `params.images`, `params.onPartialReply`, `params.onAgentEvent`.
- **Exemples réels** : `extensions/muse/index.ts`, `extensions/copilot/index.ts`,
  `extensions/acpx/index.ts`, `extensions/codex/index.ts` — tous appellent
  `api.registerAgentHarness(...)`.

**b) CLI backend plugin** — `docs/plugins/cli-backend-plugins.md` et
`docs/gateway/cli-backends.md` : `api.registerCliBackend({ id, modelProvider,
liveTest, config })`, où `config` porte `command`, `args`, `resumeArgs` (`{prompt}`,
`{sessionId}`), `output`/`resumeOutput` (`json`, `jsonl`, `text`), `jsonlDialect`
(`claude-stream-json`, `gemini-stream-json`), `input` (`arg`/`stdin`), `sessionArgs`,
`sessionMode`, `modelArg`, `modelAliases`, `env`/`clearEnv`, `imageArg`,
`bundleMcp`/`bundleMcpMode`, `nativeToolMode`, `liveSession`.

### Le mode text-only est-il la seule option ?

**Non.** La page tierce `docs.openclaw.kr/gateway/cli-backends` décrit les CLI
backends comme un filet « text in / text out, tools disabled, no streaming,
fallback only ». La documentation du dépôt amont, à jour, dit l'inverse sur les
trois points :

- « A CLI backend can be an agent's **primary runtime** or a fallback. »
- « **JSONL streaming** for CLIs that support it. »
- outils disponibles via `bundleMcp: true` et le pont MCP en boucle locale (les
  appels d'outils ne sont pas injectés dans le protocole du CLI, le CLI voit les
  outils du Gateway par MCP).

Le miroir coréen est **obsolète** : utile pour décrire le comportement conservateur
par défaut, pas l'état de l'art.

### Ce que l'adaptateur doit fournir

Un plugin qui déclare `cliBackends` dans `openclaw.plugin.json`, appelle
`api.registerCliBackend(...)` (chemin court : `command` = binaire UHB, `output:
"jsonl"`, `jsonlDialect: "claude-stream-json"`, `sessionArgs` + `sessionMode:
"existing"`), et pour le chemin natif un `AgentHarnessV2` dont `runAttempt`
implémente `execute_task`.

### Effort — 1 à 2 semaines

Contrat documenté, versionné et illustré par quatre implémentations du dépôt ; le travail réel est le flux d'événements et la politique d'outils.

### Risque — moyen

`AgentHarnessV2` est explicitement **expérimental** : « the contract is still
experimental because the parameter types intentionally mirror the current embedded
runner », et l'installation de harness tiers l'est aussi. Le chemin CLI backend est
plus stable (config déclarative) mais moins riche.

**Verdict — primary path possible, pas juste fallback ?** **Oui.** Documenté pour
les CLI backends ; expérimental mais supporté pour les agent harness.

---

## 3. DeepSeek Harness (DSH)

**Nature — runtime d'exécution, pas un control plane.** DSH détient l'agent loop, le
sandbox, les outils et le journal de session ; il ne possède ni UI produit, ni crons,
ni notifications. C'est le seul produit de cette liste déjà model-agnostic **et**
plugin-based : « tout est plugin » via Cordis, avec DeepSeek, OpenAI, Anthropic et des
endpoints personnalisés en natif. Il n'a donc pas besoin d'être rendu universel — il a
besoin d'un contrat pour être utilisable depuis un control plane.

### Contrat fourni

Plugin Cordis natif. Journal de session append-only, replayable et forkable. Agent
loop remplaçable via un waterfall `pre-step` / `request` / `request-error`. Sandbox,
outils et plugins sont des composants Cordis. Statut : **developer preview**, breaking
changes annoncés.

### Voie d'intégration

**Voie principale** — DSH runtime derrière un control plane UHB-native : le control
plane parle les 8 fonctions, l'UHB route vers DSH, DSH exécute (sandbox, outils,
journal) et appelle le modèle via son propre model adapter. Aucun protocole de modèle
n'est traduit dans cette voie : UHB transporte des tâches et des événements.

**Voie secondaire** — un plugin DSH exposant les 8 fonctions aux control planes
existants (Harness Maxxing, OpenClaw) : `execute_task` sur l'agent loop, `cancel` sur
le waterfall, `stream` sur le journal append-only (replay et fork deviennent des
capacités du backend), `health` sans inférence.

### Effort et risque

Effort : 2 à 4 semaines pour la voie secondaire ; la voie principale dépend d'un
control plane UHB-native aujourd'hui inexistant, donc non chiffrable isolément.
Risque moyen à élevé : developer preview, breaking changes annoncés, le contrat peut
bouger sans préavis. DSH est donc marqué **`supported, experimental`** (SPEC.md §6),
et un adapter DSH se teste contre une version épinglée, jamais contre `main`.

---

## 4. Hermes

**Contrat fourni** — aucun contrat d'exécution externe documenté à ce jour. Hermes
est un runtime d'agent complet (gateway, profils, skills, mémoire, plugins), pas un
control plane au sens de ce document. **[À VÉRIFIER]** : existence d'une surface
d'enregistrement de harness/backend dans le gateway.

**Voie d'intégration** — extraction des huit fonctions depuis l'existant :
configuration des providers (routage et résolution de clés), bibliothèque de skills
(chargement), fichiers de mémoire par profil (synchronisation), boucle d'exécution
du gateway (tâche, flux, annulation). L'adapter expose ensuite ces fonctions comme
un backend UHB.

**Ce que l'adaptateur doit fournir** — un module Python qui implémente les huit
fonctions, `stream()` branché sur le canal d'événements existant, `health()` sans
inférence.

**Effort — 2 à 3 semaines. Risque — élevé.** Le couplage entre gateway, profils et
outils est fort : extraire les huit fonctions sans dupliquer la logique de session
est le vrai travail. Un adapter qui réimplémente le routage divergera à la première
évolution du produit.

---

## 5. Synthèse

| Produit | Nature | Contrat d'exécution | Primary possible | Risque | Effort |
|---|---|---|---|---|---|
| Harness Maxxing | control plane | aucun (fake CLI + proxy) | oui, non contractuel | élevé | 1–3 sem. |
| OpenClaw | control plane | `registerCliBackend` + `registerAgentHarness` | oui | moyen | 1–2 sem. |
| DeepSeek Harness | runtime | plugin Cordis : les 8 fonctions sont à exposer | oui, comme backend | moyen à élevé | 2–4 sem. |
| Hermes | runtime | aucun | oui, par extraction | élevé | 2–3 sem. |

Le seul contrat **documenté comme un contrat** est celui d'OpenClaw. Les deux autres
portes existent et fonctionnent : c'est pourquoi le UHB a une valeur — nommer et
stabiliser ce que chaque produit laisse ouvert.
