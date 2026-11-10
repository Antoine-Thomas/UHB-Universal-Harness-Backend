# SPEC — UHB v0.1

Normative. **DOIT**, **DEVRAIT** et **PEUT** ont leur sens RFC 2119.

---

## 1. Définitions

**Control Plane** — la couche qui décide, gouverne et supervise. Elle possède
l'interface utilisateur, le cycle de vie des sessions, les tâches planifiées
(crons), la mémoire au niveau produit, les notifications et les politiques
(permissions, quotas, approbations). Elle ne parle pas aux modèles : elle parle au
UHB. Exemples mesurés : Harness Maxxing (sessions + crons + Telegram), OpenClaw
(Gateway + canaux + Control UI), Hermes (gateway + profils + mémoire).

**Runtime / Data Plane** — la couche qui exécute. Elle détient la boucle du modèle :
elle envoie le prompt, reçoit le texte et les appels d'outils, exécute ces outils,
gère son contexte et sa compaction, et renvoie des événements. Elle ne connaît ni
sessions produit, ni crons, ni UI.

**UHB** — le contrat d'interface entre les deux. Il définit huit fonctions, trois
protocoles de transport et une machine à états. Il ne détient ni la décision (c'est
le Control Plane) ni l'exécution (c'est le Runtime) : il traduit, route et
normalise.

Un **adapter** expose le contrat dans l'interface réelle d'un control plane donné.
Un **backend** implémente le contrat au-dessus d'un runtime donné. Les deux sont
hors périmètre de ce dépôt.

### Types de base

```ts
type MemoryLevel = "workspace" | "project" | "topic";
type StopReason  = "done" | "failed" | "cancelled" | "timeout" | "budget";

interface ToolSpec { name: string; description: string; input_schema: Record<string, unknown>; }

interface TaskContext {
  session_id: string;        // identifiant stable fourni par le control plane
  workspace: string;         // racine de travail, chemin absolu
  topic?: string;            // sous-dossier logique (projet/topic)
  system?: string;           // prompt système déjà assemblé par le control plane
  tools?: ToolSpec[];        // outils autorisés pour ce tour (vide = aucun)
  model_ref?: string;        // réf. imposée ; sinon route_model décide
  memory?: MemoryLevel;
  metadata?: Record<string, string>;
  timeout_ms?: number;
  idempotency_key?: string;  // voir §5
}

type StreamEvent =
  | { type: "start";       task_id: string; model_ref: string; at: number }
  | { type: "text";        delta: string; at: number }
  | { type: "thinking";    delta: string; at: number }
  | { type: "tool_call";   id: string; name: string; input: unknown; at: number }
  | { type: "tool_result"; id: string; ok: boolean; output: string; at: number }
  | { type: "usage";       input_tokens: number; output_tokens: number; at: number }
  | { type: "error";       code: string; message: string; retryable: boolean; at: number }
  | { type: "end";         task_id: string; reason: StopReason; at: number };

interface ModelIntent { capability: "code" | "reasoning" | "vision" | "fast" | "long-context";
                        max_cost_per_mtok?: number; min_context?: number;
                        locality?: "local" | "remote" | "any"; preferred?: string[]; }

interface ModelRef { model_ref: string; rationale?: string; }

interface ProviderDescriptor { protocol: "anthropic" | "openai" | "deepseek";
                               base_url: string; api_key_env: string; model_id: string;
                               extras?: Record<string, string>; }

interface SkillDescriptor { skill_id: string; name: string; version: string;
                            description: string; path: string; requires?: string[]; }

interface MemorySyncResult { level: MemoryLevel; path: string; bytes: number; conflict: boolean; truncated: boolean; }
interface CancelResult     { cancelled: boolean; reason: string; already_done: boolean; }
interface HealthStatus     { ok: boolean; version: string; queue_depth: number; at: number;
                             providers: Array<{ model_ref: string; reachable: boolean; latency_ms?: number }>; }
```

---

## 2. Les huit fonctions pivot

Chaque fonction DOIT exister. Une fonction sans sens pour un runtime donné DOIT
renvoyer une erreur explicite (`code: "unsupported"`), jamais un succès silencieux.

### 2.1 `execute_task`

```ts
execute_task(prompt: string, context: TaskContext): AsyncIterable<StreamEvent>
```

Lance un tour d'agent et rend ses événements au fil de l'eau. C'est la fonction
centrale : sans elle, un backend n'est pas un backend.

**Contrainte** — le premier événement émis DOIT être `start`, le dernier DOIT être
`end`. Aucun événement après `end`. Si `context.idempotency_key` est fourni, un
second appel avec la même clé NE DOIT PAS créer un second tour (§5). Le prompt ne
DOIT PAS contenir le prompt système : celui-ci voyage dans `context.system`.

```ts
const it = uhb.execute_task("corrige le test qui échoue", {
  session_id: "s-7f3a", workspace: "/work/projects/acme",
  topic: "projects/acme/topics/api", system: "Tu es un agent de code.",
  tools: [{ name: "bash", description: "shell", input_schema: { type: "object" } }],
  model_ref: "deepseek/deepseek-v4.1-flash",
});
for await (const ev of it) render(ev);

// flux rendu
{ type: "start", task_id: "t-91", model_ref: "deepseek/deepseek-v4.1-flash", at: 1770000000.1 }
{ type: "thinking", delta: "Je regarde d'abord le test…", at: 1770000000.4 }
{ type: "tool_call", id: "call_1", name: "bash", input: { cmd: "pytest -q" }, at: 1770000000.9 }
{ type: "tool_result", id: "call_1", ok: false, output: "1 failed", at: 1770000001.8 }
{ type: "text", delta: "L'assertion porte sur…", at: 1770000002.1 }
{ type: "usage", input_tokens: 1842, output_tokens: 233, at: 1770000002.4 }
{ type: "end", task_id: "t-91", reason: "done", at: 1770000002.4 }
```

### 2.2 `sync_memory`

```ts
sync_memory(level: MemoryLevel, content: string): MemorySyncResult
```

Écrit ou fusionne un bloc de mémoire au niveau demandé, sans jamais écraser le
contenu d'un autre niveau.

**Contrainte** — idempotent par contenu : réécrire le même `content` au même niveau
NE DOIT PAS produire de doublon. Un conflit (écriture concurrente) DOIT être
rapporté via `conflict: true` plutôt que résolu en silence. UHB négocie le format
avec le runtime ; il ne l'impose pas (voir NON-GOALS §5).

```ts
uhb.sync_memory("project", "## Décisions\n- API en Rust, pas en Go.");
// → { level: "project", path: "/work/projects/acme/memory.md",
//     bytes: 46, conflict: false, truncated: false }
```

### 2.3 `load_skill`

```ts
load_skill(skill_id: string): SkillDescriptor
```

Rend une procédure disponible au modèle : identifiant, version, description, chemin
lisible, dépendances.

**Contrainte** — synchrone et sans appel réseau : le modèle ne peut pas attendre un
chargement de skill. Un `skill_id` inconnu DOIT lever une erreur typée, pas
retourner un descripteur vide.

```ts
uhb.load_skill("code-quality:review");
// → { skill_id: "code-quality:review", name: "Code review", version: "1.2",
//     description: "Revue ciblée : correctness, sécurité, style",
//     path: "/skills/code-quality/SKILL.md", requires: ["git"] }
```

### 2.4 `route_model`

```ts
route_model(intent: ModelIntent): ModelRef
```

Choisit une référence de modèle à partir d'une intention (capacité, coût, contexte,
localité, préférences), et non d'un nom de fournisseur.

**Contrainte** — déterministe pour une même intention et un même état de
configuration, et DOIT documenter sa `rationale`. Elle PEUT consulter `health()` en
interne ; elle NE DOIT PAS effectuer d'inférence pour décider.

```ts
uhb.route_model({ capability: "code", min_context: 200_000, locality: "remote" });
// → { model_ref: "deepseek/deepseek-v4.1-pro",
//     rationale: "code + 256K contexte, coût < 0.60 $/Mtok" }
```

### 2.5 `stream`

```ts
stream(output: StreamEvent): void
```

Pousse un événement vers la surface de consommation (UI live). Obligatoire : c'est
ce qui rend les appels d'outils, les tokens et les diffs visibles en temps réel.

**Contrainte** — synchrone, non bloquant, ordonné. Un backend DOIT pouvoir produire
`stream` sans savoir qui consomme (SSE, WebSocket, stdout JSONL, fichier de log).
Le contrat ne fixe pas le transport : il fixe l'ordre et la forme.

```ts
uhb.stream({ type: "text", delta: "```diff\n- assert x == 1\n+ assert x == 2\n```", at: 1770000003 });
// → aucun retour ; l'événement est consommé par le canal ouvert au §2.1
```

### 2.6 `cancel`

```ts
cancel(task_id: string): CancelResult
```

Demande l'arrêt d'une tâche. L'arrêt DOIT être coopératif : le backend laisse au
runtime le soin de terminer l'exécution d'un outil en cours, puis émet `end` avec
`reason: "cancelled"`.

**Contrainte** — idempotent : annuler une tâche déjà terminée rend
`{ cancelled: false, already_done: true }` et sort en succès. Un `task_id` inconnu
rend `already_done: true` avec `reason: "unknown_task"`.

```ts
uhb.cancel("t-91");
// → { cancelled: true, reason: "user_stop", already_done: false }
```

### 2.7 `health`

```ts
health(): HealthStatus
```

État du backend : version, providers joignables, profondeur de file.

**Contrainte** — NE DOIT PAS consommer de tokens ni lancer d'inférence. DOIT
répondre en moins de 2 s même si un provider est injoignable (timeout court par
provider, résultat partiel plutôt qu'échec global).

```ts
uhb.health();
// → { ok: true, version: "uhb/0.1", queue_depth: 0, at: 1770000010,
//     providers: [{ model_ref: "deepseek/deepseek-v4.1-flash", reachable: true, latency_ms: 41 }] }
```

### 2.8 `resolve_provider`

```ts
resolve_provider(model_ref: string): ProviderDescriptor
```

Traduit une référence de modèle en coordonnées d'appel : protocole, URL de base,
variable d'environnement qui porte la clé, identifiant natif du modèle.

**Contrainte** — `api_key_env` est un **nom de variable**, jamais une valeur : le
contrat ne transporte aucun secret. Un `model_ref` non résolvable DOIT lever une
erreur typée listant les références connues.

```ts
uhb.resolve_provider("deepseek/deepseek-v4.1-flash");
// → { protocol: "deepseek", base_url: "https://api.deepseek.com/v1",
//     api_key_env: "DEEPSEEK_API_KEY", model_id: "deepseek-v4.1-flash",
//     extras: { reasoning: "on" } }
```

---

## 3. Les trois protocoles supportés

### 3.1 Anthropic — `POST /v1/messages`

Pour Claude Code et tout runtime qui imite son dialecte. C'est le protocole que la
majorité des control planes Claude-first parlent déjà.

| Champ | Statut | Note |
|---|---|---|
| `model` | requis | identifiant natif du modèle |
| `max_tokens` | requis | plafond de sortie |
| `messages[]` | requis | `role: user\|assistant`, `content` texte ou blocs |
| `system` | optionnel | chaîne ou blocs |
| `tools[]` | optionnel | `{name, description, input_schema}` |
| `tool_choice` | optionnel | `auto\|any\|tool` |
| `stream` | optionnel | `true` → SSE `message_start`, `content_block_delta`, `message_stop` |
| `stop_sequences`, `metadata` | optionnel | |

Réponse non-streamée : `{ id, type: "message", role, content: Block[], stop_reason,
usage }` où `Block` = `{type:"text",text}` | `{type:"tool_use",id,name,input}` |
`{type:"thinking",thinking}`. En flux, chaque delta est un `content_block_delta`
avec `index` — l'index doit être préservé, sinon les diffs d'outils arrivent dans le
désordre.

### 3.2 OpenAI — `POST /v1/chat/completions`

Pour DeepSeek, Kimi, GLM, OpenAI, Ollama, vLLM, et la majorité des providers. C'est
le plus petit dénominateur commun du marché.

| Champ | Statut | Note |
|---|---|---|
| `model` | requis | identifiant natif |
| `messages[]` | requis | `role: system\|user\|assistant\|tool` |
| `max_tokens` | optionnel | |
| `tools[]` | optionnel | `{type:"function", function:{name, description, parameters}}` |
| `tool_choice` | optionnel | `auto\|none\|required\|{...}` |
| `stream` | optionnel | `true` → SSE `data: {...}` + `data: [DONE]` |
| `stream_options` | optionnel | `{include_usage: true}` |
| `temperature`, `top_p` | optionnel | |

Réponse : `{ id, choices:[{ index, message:{role, content, tool_calls?},
finish_reason }], usage }`. `tool_calls[].function.arguments` est une **chaîne
JSON**, pas un objet — la traduction vers Anthropic DOIT parser, et échouer
proprement sur JSON invalide.

### 3.3 DeepSeek natif — `POST /v1/chat/completions` + extensions

Même endpoint que 3.2, avec des champs supplémentaires. Utiliser ce protocole
seulement quand le backend doit exposer les capacités spécifiques ; sinon 3.2 suffit
et reste portable.

| Extension | Statut | Note |
|---|---|---|
| `reasoning_content` | optionnel (réponse) | raisonnement séparé du texte visible |
| `reasoning_effort` | optionnel (requête) | budget de raisonnement |
| `messages[].reasoning_content` | optionnel | à rejouer ou non selon le modèle |
| `tool_calls` parallèles | optionnel | plusieurs appels dans un même message |
| `usage.prompt_cache_hit_tokens` | optionnel | cache de préfixe |

Règle de traduction : `reasoning_content` DOIT devenir un bloc `thinking` côté
Anthropic, jamais du texte visible. Un backend qui ne sait pas le distinguer DOIT le
supprimer plutôt que le fusionner.

---

## 4. Le pattern « proxy de protocole »

UHB peut se brancher derrière un control plane Claude-first **sans patcher le control
plane**. C'est le pattern LiteLLM, documenté et éprouvé (voir
[ADAPTERS.md](ADAPTERS.md) §1 et §2).

1. Le control plane croit parler à Anthropic : il envoie `POST /v1/messages` avec ses
   outils et son prompt système.
2. UHB reçoit la requête, résout le modèle demandé (`resolve_provider`), choisit le
   protocole cible.
3. UHB traduit vers OpenAI `/v1/chat/completions` : `system` → message `system`,
   blocs `tool_use` → `tool_calls`, blocs `tool_result` → messages `role: "tool"`,
   `input_schema` → `parameters`.
4. Le runtime reçoit la requête traduite, exécute, répond en SSE OpenAI.
5. UHB retraduit la réponse : `tool_calls` → blocs `tool_use`, `content` → blocs
   `text`, `reasoning_content` → blocs `thinking`, `finish_reason` → `stop_reason`.
6. Le control plane reçoit un flux Anthropic valide et ne sait pas que le modèle
   n'est pas Claude.

Trois règles font la différence entre un proxy qui marche et un proxy qui ment :

- **Les identifiants d'outils survivent.** `tool_use.id` et `tool_call.id` doivent
  être stables de bout en bout ; un `tool_result` orphelin casse la boucle.
- **Les raisons de fin sont traduites, pas copiées.** `length` → `max_tokens`,
  `tool_calls` → `tool_use`, `stop` → `end_turn`. Un `stop_reason` inconnu DOIT être
  mappé sur `end_turn` et signalé dans les logs, jamais laissé tel quel.
- **Le flux reste un flux.** Le proxy DOIT relayer les deltas au fur et à mesure.
  Bufferiser la réponse pour la traduire casse l'UI live et le streaming d'outils,
  qui sont la raison d'être du contrat.

---

## 5. Les états d'une tâche

```
   submit ──▶ queued ──▶ working ──▶ streaming ──▶ done
                          │             │
                          └─────────────┴──▶ failed      (erreur, timeout, budget)
                                             ▶ cancelled   (cancel())
```

| De | Vers | Déclencheur |
|---|---|---|
| `queued` | `working` | le runtime accepte la tâche |
| `working` | `streaming` | premier `text`, `thinking` ou `tool_call` |
| `streaming` | `done` | `end(reason: "done")` |
| `working`, `streaming` | `failed` | `error` non retryable, `timeout` ou `budget` |
| `working`, `streaming` | `cancelled` | `cancel()` accepté |

Une transition non listée est une erreur de contrat : elle DOIT être signalée, pas
absorbée.

**Idempotence.** Une tâche est identifiée par `task_id`, stable de `start` à `end`.
`idempotency_key` (optionnel, fourni par le control plane) garantit qu'un rejeu —
réseau coupé, UI rechargée, message renvoyé — ne crée pas un second tour : le backend
rend le flux de la tâche existante ou une erreur `duplicate` explicite. `cancel` et
`health` sont idempotents par nature. `sync_memory` l'est par contenu. `load_skill`,
`route_model` et `resolve_provider` sont purs : mêmes entrées, même sortie, aucun
effet de bord.

---

## 6. Compatibilité

Mesuré en Phase 0 sur les dépôts publics ; preuves dans [ADAPTERS.md](ADAPTERS.md).

| Control Plane / Runtime | Support | Voie d'intégration | Coût estimé |
|---|---|---|---|
| **Harness Maxxing** | Partiel | fake CLI via `MAXXING_CLAUDE_BIN`, ou proxy Anthropic (`ANTHROPIC_BASE_URL`) | 1–3 semaines |
| **OpenClaw** | Oui | plugin `agent harness` natif (`runAttempt`) ou CLI backend (`api.registerCliBackend`) | 1–2 semaines |
| **DeepSeek Harness (DSH)** | Runtime, `experimental` | plugin DSH (voie secondaire) ou runtime UHB-native (voie principale) | 2–4 semaines |
| **Hermes** | Partiel | extraction des 8 fonctions depuis le gateway et les profils | 2–3 semaines |

Note — DSH est un cas à part : déjà model-agnostic et plugin-based, il n'a pas
besoin d'être rendu universel. UHB lui fournit le contrat d'interface qui permet à
n'importe quel control plane de l'utiliser sans apprendre Cordis ni son API.

Lecture : « Support » qualifie la capacité du produit à héberger un backend externe
**sans être patché**. « Runtime » = le produit exécute, c'est un backend candidat et
non un hôte ; « Partiel » = une porte existe, sans contrat d'exécution documenté.
