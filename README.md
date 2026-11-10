# UHB — Universal Harness Backend

**UHB est le contrat d'interface qui permet à n'importe quel Control Plane
d'utiliser n'importe quel backend d'exécution, quel que soit le modèle. Il rend
les harness Claude-first compatibles avec DeepSeek, Kimi, GLM, OpenAI, et tout
modèle futur.**

Statut : **v0.1 — spec only**. Ce dépôt contient un contrat, pas une
implémentation. Aucun code, aucun binaire, aucun adapter livré.

---

## 1. Le problème

Les control planes d'agents qui comptent aujourd'hui — Harness Maxxing, OpenClaw,
Hermes — ont été construits autour de Claude Code. Ce n'est pas une décision
technique, c'est une histoire : Claude Code a fourni le premier CLI d'agent
utilisable en production, avec sessions persistantes, appels d'outils, streaming
et compaction. Les harness ont donc pris l'habitude de lancer `claude`, de
consommer son format `stream-json` et de traiter son vocabulaire comme un
standard de fait. Résultat : un utilisateur qui possède DeepSeek v4.1 ou Kimi n'a
pas de chemin propre. Chaque harness a sa propre notion de « modèle exécutable »,
quand il en a une, et chacune est différente.

Anthropic a par ailleurs fermé explicitement la porte au routage d'un modèle
non-Claude via les abonnements Claude Max. C'est une décision d'Anthropic, pas un
bug à contourner : toute intégration qui suppose un abonnement Claude Max pour
faire tourner DeepSeek est une impasse, et UHB ne la propose pas. Ce qui reste —
et que ce contrat rend explicite — ce sont les voies légitimes : un endpoint HTTP
compatible, un binaire de CLI substituable, un plugin d'exécution natif.

## 2. La solution

Deux artefacts, et rien d'autre :

1. **Un contrat standard** — huit fonctions pivot ([SPEC.md](SPEC.md) §2) qui
   décrivent tout ce qu'un control plane demande à un backend d'exécution :
   lancer une tâche, la streamer, l'annuler, synchroniser la mémoire, charger un
   skill, router un modèle, résoudre un provider, répondre sur la santé.
2. **Un adaptateur par control plane** ([ADAPTERS.md](ADAPTERS.md)) — la pièce
   qui traduit le contrat vers l'interface réelle du control plane : proxy HTTP,
   CLI substituable, ou plugin natif.

Un backend qui implémente les huit fonctions est utilisable depuis n'importe quel
control plane qui possède un adaptateur. Un control plane qui possède un
adaptateur peut utiliser n'importe quel backend. Le produit cartésien est le but.

## 3. Les trois couches

| Couche | Rôle | Exemples |
|---|---|---|
| **Control Plane** | décide, gouverne, supervise : UI, sessions, crons, mémoire, notifications | Harness Maxxing, OpenClaw, Hermes |
| **UHB** | traduit et arbitre : routage de modèle, traduction de protocole, streaming normalisé, annulation, santé | ce contrat |
| **Runtime** | exécute : appelle le modèle, exécute les outils, renvoie des événements | DeepSeek v4.1, Kimi, GLM, OpenAI, Ollama, Claude |

Les flux, les points d'extension et les invariants sont détaillés dans
[ARCHITECTURE.md](ARCHITECTURE.md).

### Les huit fonctions, en une ligne

| # | Fonction | Ce qu'elle règle |
|---|---|---|
| 1 | `execute_task` | lancer un tour et recevoir ses événements en flux |
| 2 | `sync_memory` | écrire la mémoire au bon niveau (workspace, projet, topic) |
| 3 | `load_skill` | rendre une procédure disponible au modèle |
| 4 | `route_model` | choisir un modèle à partir d'une intention |
| 5 | `stream` | garantir un flux utilisable par une UI live |
| 6 | `cancel` | arrêter proprement une tâche en cours |
| 7 | `health` | répondre de l'état du backend sans consommer de tokens |
| 8 | `resolve_provider` | traduire un nom de modèle en endpoint, protocole, clé |

## 4. Ce que UHB n'est pas

UHB n'est pas un control plane, pas un runtime, pas un modèle, pas un format de
stockage, et pas un contournement des restrictions d'Anthropic. La liste complète,
avec les raisons, est dans [NON-GOALS.md](NON-GOALS.md).

Le sigle est **UHB** — Universal Harness **Backend**. Le *Runtime* héberge et
survit (processus, sandbox, reprise après crash) ; le *Backend* exécute et
s'interchange. On est sur Backend. Le dépôt est nommé en toutes lettres,
`UHB-Universal-Harness-Backend`, et non `universal-harness-*` : le sigle **UHP**
(Unified Harness Protocol) existe déjà et désigne un concept proche, qu'il ne faut
pas confondre avec celui-ci.

## 5. Pour qui

**Développeurs de control planes.** Si votre harness lance `claude` en dur et que
vous voulez arrêter sans réécrire votre produit, un adaptateur suffit : le contrat
décrit ce qu'il faut exposer, pas comment réorganiser votre code.

**Utilisateurs de modèles non-Claude.** Si vous payez DeepSeek, Kimi, GLM ou un
endpoint local, vous ne devriez pas dépendre du vocabulaire d'un fournisseur
concurrent. Le contrat parle en intentions et en références de modèle, pas en
noms de fournisseur.

**Mainteneurs de harness alternatifs.** Si vous réimplémentez une boucle d'agent
en Python, en Go ou en Rust, le contrat définit la surface minimale à exposer pour
être utilisable depuis les control planes existants — et testable sans UI.

## 6. Comment contribuer

Un adapter par PR, doc d'abord : le code vient après validation du contrat. Une
évolution de spec passe par une issue puis un diff de `SPEC.md`. Voir
[CONTRIBUTING.md](CONTRIBUTING.md).

## 7. Références

- **Harness Maxxing** — https://github.com/makushingu/harness-maxxing — Fair
  Source (source-available, pas OSI) : control plane self-hosted construit sur le
  CLI Claude Code. Lance `claude -p --output-format stream-json --verbose`, garde
  le binaire substituable via `MAXXING_CLAUDE_BIN`.
- **OpenClaw** — https://github.com/openclaw/openclaw — MIT : Gateway + deux
  surfaces d'exécution documentées (`api.registerCliBackend` et
  `api.registerAgentHarness`), plus un pont MCP optionnel pour les outils.
- **DeepSeek V4.1 (pattern proxy)** —
  https://github.com/shi3z/deepseekv4.1-A100-custom — `examples/claude_code/` :
  Claude Code → LiteLLM → moteur d'inférence, traduction Anthropic ↔ OpenAI.
- **LiteLLM** — https://github.com/BerriAI/litellm — la référence éprouvée de la
  traduction de protocole Anthropic ↔ OpenAI.
- `docs.openclaw.kr/gateway/cli-backends` — miroir tiers de la documentation
  OpenClaw. Il décrit les CLI backends comme un mode « text-only, fallback
  uniquement » ; la doc du dépôt amont dit l'inverse (backend primaire, outils via
  pont MCP, JSONL). Les écarts mesurés sont listés dans
  [ADAPTERS.md](ADAPTERS.md) §2.

## 8. État du dépôt

Ce qui existe aujourd'hui : la spec, et rien d'autre.

| Fichier | Rôle |
|---|---|
| [SPEC.md](SPEC.md) | le contrat normatif : 8 fonctions, 3 protocoles, machine à états |
| [ARCHITECTURE.md](ARCHITECTURE.md) | les 3 couches, le flux, les points d'extension, les invariants |
| [ADAPTERS.md](ADAPTERS.md) | état mesuré de chaque control plane connu |
| [NON-GOALS.md](NON-GOALS.md) | ce que UHB refuse de faire, et pourquoi |
| [CONTRIBUTING.md](CONTRIBUTING.md) | comment proposer un adapter ou une évolution |

Ce qui n'existe pas encore, et qui viendra après validation du contrat : un backend
de référence (les 8 fonctions au-dessus d'un runtime OpenAI-compatible), un proxy
Anthropic ↔ OpenAI, et un premier adapter — OpenClaw, parce que c'est le seul dont
le contrat d'exécution soit documenté comme un contrat.

Ordre de lecture conseillé : ce README, puis `SPEC.md` §2, puis `ARCHITECTURE.md`,
puis `ADAPTERS.md`. Le reste est du contexte.

Une remarque sur le contrat passe par une issue `Spec: <sujet>` ; un nouveau
control plane par une issue `Adapter: <nom>`. Voir [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — voir [LICENSE](LICENSE). Le contrat est libre de réimplémentation ; les
marques, licences et marques déposées des control planes cités restent celles de
leurs auteurs. Ce dépôt ne contient ni ne redistribue leur code.
