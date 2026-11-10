# CONTRIBUTING

Deux types de contribution, deux procédures. Aucune des deux n'accepte de code
dans ce dépôt tant que le contrat n'est pas validé.

---

## Proposer un adapter

1. **Ouvrir une issue** intitulée `Adapter: <nom du control plane>`. Y répondre en
   trois lignes : quelle interface d'exécution le produit expose réellement, quelle
   porte est utilisable, ce qui manque.
2. **Décrire la voie d'intégration** parmi les trois connues :
   - **natif** — le produit expose une interface d'enregistrement d'exécution
     (ex. `registerAgentHarness`, `registerCliBackend`) ;
   - **proxy** — le produit ne sait parler qu'un dialecte (souvent Anthropic
     `/v1/messages`) et accepte un `base_url` ou une variable d'environnement ;
   - **fake CLI** — le produit lance un binaire par son nom ou par une variable
     d'environnement, et consomme sa sortie.
3. **Fournir un document** `ADAPTERS/<nom>.md` avec : contrat réellement fourni,
   preuves (chemins de fichiers, docs lues, version), voie retenue, ce que
   l'adaptateur doit fournir, effort estimé, risque et sa cause.
4. **Ouvrir la PR avec la doc seule.** Le code vient après validation, dans un
   dépôt séparé.

Une fiche d'adapter sans preuve mesurée n'est pas fusionnée. Une affirmation
plausible mais non vérifiée se marque `[À VÉRIFIER]` — c'est acceptable et utile ;
une affirmation inventée ne l'est pas.

## Proposer une évolution de la spec

1. **Ouvrir une issue** intitulée `Spec: <sujet>`.
2. **Décrire** le cas d'usage réel qui motive le changement, l'impact sur les huit
   fonctions pivot, et ce qui casse si on ne le fait pas.
3. **Ouvrir la PR** avec le diff de `SPEC.md` uniquement. Une évolution qui touche
   `ARCHITECTURE.md` ou `ADAPTERS.md` dans la même PR doit le justifier
   explicitement.

Changement cassant = version mineure incrémentée (`v0.1` → `v0.2`) et section
« Compatibilité » mise à jour.

## Règles de fond

- **Pas de secret, pas de chemin local.** Aucun token, aucune clé, aucun chemin
  personnel dans un document. Les exemples de clé sont des **noms** de variables
  (`DEEPSEEK_API_KEY`), jamais des valeurs.
- **Pas de nom de fournisseur dans le contrat.** Le contrat parle en intentions et
  en références de modèle. Un exemple peut citer un fournisseur ; une règle ne le
  peut pas.
- **Mesurer avant d'affirmer.** Un comportement de control plane se prouve par un
  fichier ou une doc à jour. Une page tierce ou un miroir peut être obsolète : le
  dire quand on s'en écarte.
- **Une règle par paragraphe.** Un contrat se relit ; une phrase qui contient deux
  obligations sera mal implémentée.

## Format des documents

Markdown, français, phrases courtes. **DOIT**, **DEVRAIT**, **PEUT** au sens
RFC 2119 dans `SPEC.md`. Les blocs de code sont des exemples de contrat
(TypeScript de préférence), pas une implémentation.

## Licence des contributions

MIT, inbound = outbound (voir [LICENSE](LICENSE)). En ouvrant une PR, vous
acceptez que votre texte soit publié sous cette licence. Aucun CLA n'est demandé.
