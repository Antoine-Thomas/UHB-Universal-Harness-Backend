# NON-GOALS — ce que UHB ne fait pas

Un contrat vaut autant par ce qu'il exclut que par ce qu'il définit. Chaque
non-goal ci-dessous est une décision, pas un manque.

---

## 1. UHB ne remplace pas Claude Code pour qui a un abonnement Claude Max

UHB n'existe pas pour « libérer » un utilisateur satisfait de son abonnement
Claude. Il existe pour celui qui veut utiliser DeepSeek, Kimi, GLM, un endpoint
local ou n'importe quel modèle, sans être marié à un vendor — y compris un
utilisateur qui garde Claude pour certaines tâches et un autre modèle pour
d'autres.

Conséquence concrète : aucun objectif de parité fonctionnelle avec Claude Code.
Si une capacité n'existe que dans Claude Code (par exemple un outil propriétaire,
une fonction de compaction interne), UHB ne la réimplémente pas.

## 2. UHB ne contourne pas les restrictions d'Anthropic

Anthropic a fermé explicitement le routage d'un modèle non-Claude via les
abonnements Claude Max. C'est une décision d'Anthropic, prise par l'éditeur du
produit, et ce n'est pas un bug.

UHB ne propose donc ni partage d'identifiants, ni contournement de quota, ni
falsification d'en-têtes d'authentification, ni réutilisation d'un jeton
d'abonnement pour un autre modèle. Ce qu'il propose est documenté comme tel : un
endpoint HTTP compatible, un binaire de CLI substituable, un plugin d'exécution
natif. Si un produit refuse une configuration, c'est à son éditeur d'en décider ;
le rôle de UHB est de rendre explicite ce qui reste ouvert.

## 3. UHB n'est pas un control plane

UHB ne gère pas les sessions au niveau produit, l'interface utilisateur, les
tâches planifiées, les notifications, la mémoire au niveau produit, les
permissions d'équipe, ni la facturation. Tout cela appartient au control plane.

UHB tient un état de tâche en mémoire — pour annuler, pour dédupliquer, pour
répondre sur la santé — et rien d'autre. Un backend UHB qui redémarre perd cet
état ; une tâche en cours est perdue, et c'est au control plane de la relancer.

## 4. UHB n'est pas un modèle

UHB ne fait pas d'inférence. Il ne possède pas de poids, ne fait pas de
tokenisation, ne calcule pas de logits, ne fait pas de RAG, ne choisit pas un
échantillonnage.

Il route vers un modèle et traduit un protocole. Un UHB qui « améliore » une
réponse en la réécrivant n'est plus un contrat : c'est un proxy qui ment sur son
contenu. La seule transformation autorisée est structurelle (formats, champs,
raisons de fin), jamais sémantique.

## 5. UHB n'est pas un format de stockage

UHB négocie le format de mémoire avec le runtime via `sync_memory`, il ne
l'impose pas. Il ne définit ni schéma de base, ni format de fichier, ni arbre de
répertoires normatif.

Deux backends peuvent stocker la mémoire différemment — un fichier markdown par
niveau, une base SQLite, un index vectoriel — et rester conformes, tant que
`sync_memory` écrit au niveau demandé, ne déborde pas sur un autre niveau et
rapporte les conflits.

## 6. Autres exclusions assumées

- **Pas de code dans ce dépôt.** UHB v0.1 est une spec. Aucun adapter, aucun
  binaire, aucun serveur. Le code viendra après validation du contrat.
- **Pas de sélection de modèle par défaut.** `route_model` choisit à partir d'une
  intention ; UHB ne décide pas à la place de l'utilisateur quel modèle est
  « le meilleur ».
- **Pas de télémétrie.** Le contrat ne prévoit aucun report d'usage vers un
  serveur tiers. Les compteurs de tokens restent locaux, exposés par `usage` dans
  le flux.
- **Pas d'interface graphique.** UHB n'a pas d'UI, pas de tableau de bord, pas de
  page web de configuration. L'UI appartient au control plane.
- **Pas de gestion de secrets.** Le contrat transporte des **noms** de variables
  d'environnement, jamais des valeurs. Un coffre-fort, un vault, un `.env` : c'est
  le rôle du déploiement, pas du contrat.

---

## Ce que ces exclusions protègent

Un contrat qui essaie de tout faire devient un produit, et un produit se marie
avec son écosystème. Chaque non-goal ci-dessus garde une frontière nette : le
control plane décide, le runtime exécute, UHB traduit. C'est cette frontière qui
permet à un harness Claude-first d'utiliser DeepSeek sans réécrire son produit, et
à un runtime local d'être utilisable depuis trois UIs différentes sans connaître
aucune des trois.
