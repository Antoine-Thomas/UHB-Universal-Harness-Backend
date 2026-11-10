# ARCHITECTURE — UHB

Comment les trois couches s'articulent, ce qui circule entre elles, où l'on
branche un nouveau composant, et ce qui ne change jamais.

---

## 1. Les trois couches

```
┌─ CONTROL PLANE ─────────────────────────┐
│ Harness Maxxing · OpenClaw · Hermes     │
│ (UI, sessions, crons, mémoire)          │
└────────────────┬────────────────────────┘
                 │ HTTP (Anthropic ou OpenAI)
                 ▼
┌─ UHB (Universal Harness Backend) ──────┐
│ resolve_provider(model_ref)             │
│ stream() · cancel() · health()          │
│ Traduction Anthropic ↔ OpenAI           │
└────────────────┬────────────────────────┘
                 │ HTTP (protocole natif)
                 ▼
┌─ RUNTIME ───────────────────────────────┐
│ DeepSeek v4.1 · Kimi · GLM · Claude     │
│ Ollama · OpenAI · local                 │
└─────────────────────────────────────────┘
```

**Control Plane.** Il possède l'état produit : quelle session, quel projet, quelle
politique, quel cron, quelle notification. Il décide *quoi* faire et *pour qui*. Il
ne sait pas quel protocole parle le modèle, et c'est volontaire : c'est ce qui
permet de changer de modèle sans changer l'UI.

**UHB.** Il possède la traduction : une intention de modèle devient une référence,
une référence devient un endpoint et un protocole, une requête dans un dialecte
devient une requête dans un autre, un flux propriétaire devient un flux normalisé.
Il possède aussi le cycle de vie de la tâche : `queued`, `working`, `streaming`,
`done`, `failed`, `cancelled` (voir [SPEC.md](SPEC.md) §5).

**Runtime.** Il possède l'exécution : la boucle du modèle, les appels d'outils, le
contexte, la compaction, les caches de préfixe. Il ne connaît ni sessions produit
ni UI. Un runtime qui expose `/v1/chat/completions` peut déjà être branché ; un
runtime qui expose `/v1/messages` aussi.

**Pourquoi trois couches et pas deux.** Fusionner UHB et le runtime rend le
runtime responsable du routage : chaque nouveau modèle obligerait à redéployer le
runtime. Fusionner UHB et le control plane rend le control plane responsable du
protocole : chaque nouveau modèle obligerait à redéployer le produit. La couche
mince au milieu est celle qui bouge le plus souvent, et c'est exactement celle
qu'on veut pouvoir remplacer sans rien casser.

---

## 2. Le flux d'une requête

De l'UI jusqu'au modèle, en six étapes. Exemple : l'utilisateur tape un message
dans une session dont le modèle est `deepseek/deepseek-v4.1-flash`.

1. **Le control plane assemble le tour.** Il choisit la session, le projet, le
   prompt système, la politique d'outils et la mémoire. Il produit un
   `TaskContext` et un prompt utilisateur. Rien n'est encore parti sur le réseau.
2. **Le control plane appelle le backend.** Selon son interface réelle, il appelle
   `execute_task` (plugin natif), ou envoie `POST /v1/messages` à un proxy local
   (fake CLI / endpoint compatible). C'est la seule différence entre les deux
   voies : le contrat est le même.
3. **UHB résout le modèle.** `route_model` si le control plane n'a pas imposé de
   référence, puis `resolve_provider` : protocole, `base_url`, `api_key_env`,
   `model_id`. La clé est lue dans l'environnement au dernier moment, jamais
   transportée par le contrat.
4. **UHB traduit la requête.** Si le control plane parle Anthropic et le runtime
   OpenAI, blocs `tool_use`/`tool_result` deviennent `tool_calls` et messages
   `role: "tool"` ; `system` devient un message système ; `input_schema` devient
   `parameters`. Si le control plane parle déjà le bon dialecte, l'étape est un
   passe-plat.
5. **Le runtime exécute et streame.** Il émet son flux natif (SSE OpenAI, SSE
   Anthropic, JSONL de CLI). Chaque delta de texte, de raisonnement, d'appel
   d'outil et de résultat est un événement.
6. **UHB retraduit et normalise.** Chaque delta devient un `StreamEvent` typé,
   poussé par `stream()` dans l'ordre d'arrivée. Le control plane affiche : tokens
   qui s'écrivent, cartes d'outils, diffs. À la fin, `end` ferme la tâche et fige
   son état.

Deux chemins sont légitimes et produisent le même résultat observable :

- **Chemin plugin** — le control plane expose une interface d'exécution (OpenClaw
  `registerAgentHarness` / `registerCliBackend`). L'adapter s'y enregistre et les
  huit fonctions sont appelées directement.
- **Chemin proxy** — le control plane ne sait parler qu'à Claude. UHB écoute en
  HTTP sur `127.0.0.1:PORT` et imite `/v1/messages`. Aucune ligne du control plane
  n'est modifiée ; c'est le chemin de Harness Maxxing et de tout produit qui lance
  `claude` en dur.

---

## 3. Les points d'extension

Trois axes d'extension, indépendants. On peut en ajouter un sans toucher aux
autres.

### 3.1 Ajouter un control plane

1. Écrire `ADAPTERS/<nom>.md` : interface réelle, voie d'intégration, effort,
   risque.
2. Choisir la voie : natif (plugin), proxy (endpoint compatible), ou CLI
   substituable.
3. Implémenter les huit fonctions dans le dialecte du control plane. Une fonction
   non supportée rend `unsupported`, jamais un faux succès.
4. Prouver un tour complet : un prompt, un appel d'outil, un diff, un `cancel`.

Ce qui ne change pas : ni le contrat, ni les protocoles, ni la machine à états.

### 3.2 Ajouter un runtime

1. Vérifier le protocole réel : `/v1/messages`, `/v1/chat/completions`, ou un
   dialecte maison.
2. Ajouter une entrée dans la table de résolution : `model_ref` → protocole,
   `base_url`, `api_key_env`, `model_id`.
3. Déclarer les capacités réelles : outils ? streaming incrémental ? raisonnement
   séparé ? vision ? contexte maximum ?
4. Vérifier `health()` sans inférence : un `GET /health` ou `GET /v1/models`.

Ce qui ne change pas : le contrat reste les huit fonctions ; un runtime n'a jamais
à connaître le control plane.

### 3.3 Ajouter un protocole

1. Documenter le schéma requête/réponse dans [SPEC.md](SPEC.md) §3, avec les
   champs obligatoires et optionnels.
2. Fournir les deux traductions : vers OpenAI (le pivot), et depuis Anthropic (le
   dialecte d'entrée le plus répandu).
3. Définir le mapping des raisons de fin et des identifiants d'outils.
4. Documenter ce qui est perdu : un protocole qui ne porte pas le raisonnement
   séparé ne doit pas le faire passer pour du texte.

Ce qui ne change pas : le pivot reste OpenAI `/v1/chat/completions`, parce que
c'est ce que la majorité des providers implémentent.

---

## 4. Les invariants

Ce qui reste vrai quel que soit l'adapter, le runtime ou le protocole. Un
composant qui viole un invariant n'est pas conforme, même s'il « marche ».

1. **Aucun secret dans le contrat.** `resolve_provider` rend un **nom** de
   variable d'environnement, jamais une valeur. Le contrat ne transporte ni clé,
   ni token, ni cookie.
2. **Aucune inférence hors d'`execute_task`.** `health`, `route_model`,
   `cancel`, `load_skill`, `resolve_provider` sont sans tokens. Un modèle ne doit
   pas être facturé pour avoir répondu « je vais bien ».
3. **Un flux ordonné, un seul `end`.** `start` d'abord, `end` en dernier, jamais
   d'événement après. Un flux interrompu sans `end` est une erreur de contrat, pas
   une fin normale.
4. **La tâche est l'unité d'annulation.** `cancel(task_id)` ne tue pas un
   processus arbitraire : il demande l'arrêt de la tâche nommée, coopérativement,
   et l'état final est `cancelled`.
5. **Le protocole du control plane ne change pas.** Un control plane qui parle
   Anthropic continue de parler Anthropic. C'est UHB qui s'adapte, jamais le
   produit hôte.
6. **UHB ne détient aucun état produit.** Pas de session, pas de cron, pas de
   mémoire produit, pas de notification. Il peut tenir un état de tâche en mémoire
   pour l'annulation et l'idempotence, et rien de plus.
7. **L'échec est explicite.** Une fonction non supportée rend `unsupported`. Un
   modèle non résolvable rend `unknown_model_ref`. Un flux invalide rend une
   erreur. Le silence et le faux succès sont interdits.
8. **La mémoire est négociée, pas imposée.** `sync_memory` écrit au niveau demandé
   et rapporte les conflits ; il ne réorganise jamais la mémoire d'un autre
   niveau.

---

## 5. Déploiement typique

```
poste de travail / serveur
├── control plane (UI web, port 3000)
├── UHB proxy (127.0.0.1:8101, Anthropic-compatible)
└── runtime
    ├── distant  → https://api.deepseek.com/v1     (clé dans l'env)
    └── local    → http://127.0.0.1:8000/v1        (moteur auto-hébergé)
```

Trois points d'attention, tirés de l'état de l'art mesuré :

- **Écouter en boucle locale uniquement.** Un proxy qui traduit Anthropic ↔ OpenAI
  expose une surface d'exécution ; `0.0.0.0` par défaut est une faute.
- **Synchroniser la fenêtre de contexte déclarée.** Si le control plane croit à
  200K et que le runtime en accepte 128K, la compaction se déclenche trop tard et
  tous les tours échouent (cas réel documenté côté DeepSeek V4.1).
- **Un cache de préfixe change tout en usage agentique.** Chaque tour d'outil
  renvoie tout l'historique : sans réutilisation de préfixe, le coût et la latence
  explosent au 20e tour. Le contrat n'impose rien, mais un backend DEVRAIT exposer
  la capacité si le runtime l'a.

---

## 6. Quand ça casse

Trois pannes reviennent dans tous les projets de ce type ; les nommer ici évite de
les redécouvrir.

- **Un flux sans `end`.** Le control plane voit des tokens, puis plus rien : l'UI
  reste « en cours » indéfiniment. Le backend DOIT fermer la tâche (`failed` ou
  `cancelled`) même quand le runtime meurt sans le dire.
- **Un `tool_result` orphelin.** L'identifiant d'appel a changé au passage d'un
  protocole ; le runtime ne reconnaît plus son propre résultat et reboucle. La
  traduction DOIT conserver l'identifiant, jamais le régénérer.
- **Une fenêtre de contexte déclarée fausse.** Le control plane croit à 200K, le
  runtime accepte 128K : les derniers tours échouent et la compaction se déclenche
  trop tard. La valeur publiée par `health()` DOIT être la valeur réelle du runtime.

Dans les trois cas, la règle est la même : rendre la panne visible — un événement
`error`, un état terminal, une valeur mesurée — plutôt que de la laisser se traduire
en silence en comportement bizarre.
