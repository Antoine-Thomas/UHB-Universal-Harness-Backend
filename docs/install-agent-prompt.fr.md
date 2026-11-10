# Installer ou mettre à jour UHB — prompt pour agent

![Status](https://img.shields.io/badge/status-v0.3-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

Version française de [`install-agent-prompt.md`](install-agent-prompt.md), qui
reste la version de référence : en cas d'écart entre les deux fichiers, l'anglais
fait foi.

Un prompt à confier à un agent de code pour amener un checkout UHB à une commande
`uhb` fonctionnelle, sous Windows, macOS ou Linux, sans rien modifier hors du
dépôt. Tous les chemins et tous les nombres ci-dessous viennent de l'exécution
réelle des commandes sur la machine de référence, pas de ce que l'arborescence
était censée être.

Colle le bloc situé entre les marqueurs `---` dans l'agent. Quatre faits le
distinguent d'une recette d'installation générique :

**Le checkout peut ne pas contenir le code.** Un commit antérieur à la livraison
du packaging porte `SPEC.md`, `ARCHITECTURE.md` et ni `uhb/`, ni `pyproject.toml`,
ni `install.sh`. Toute tentative d'installation échoue alors sur
`No such file or directory`, quelle que soit la commande — la présence de
`install.sh` et de `pyproject.toml` est donc vérifiée *avant* toute installation.

**`install.sh` fait confiance à `$PYTHON`, et `python3` n'est pas toujours le bon.**
`install.sh:51` lit `PYTHON="${PYTHON:-python3}"`. Sur une machine où `python3`
pointe vers un venv de harness, le `.venv` du projet est construit avec cet
interpréteur au lieu d'un vrai CPython 3.11+. Passer `PYTHON` explicitement.

**Il n'y a pas de chargeur `.env`.** La configuration est `uhb/config.yaml`, ou le
chemin indiqué dans `$UHB_CONFIG` ; les identifiants sont lus dans
l'environnement du processus, par nom de variable. Un chemin de config inventé
échoue au premier appel, pas à l'installation.

**Modifier le PATH est dangereux ici.** Le harness hôte est lui-même un citoyen du
PATH : une réécriture qui réordonne les entrées change le binaire que l'hôte
résout.

---

## Le prompt

```text
Tâche : installer ou mettre à jour UHB proprement sous Windows 10/11, macOS ou
Linux, sans casser l'environnement existant et sans supposer que le checkout est
complet.

Dépôt     : Antoine-Thomas/UHB-Universal-Harness-Backend
Paquet    : uhb-harness (PyPI) — la commande est `uhb`
Prérequis : Python >= 3.11, git.

## 0. Détection de l'environnement (sortie brute)
- OS : Windows (Git Bash ou PowerShell), macOS, Linux.
- Candidats Python >= 3.11 uniquement. Sous Windows, `python3` peut être un venv
  de harness sans pip — ne l'utilise PAS. Trouve un vrai Python 3.11+ et passe-le
  explicitement à install.sh (voir §2).
- Vérifie le candidat avant de lui faire confiance :
    <python> -c "import sys; print(sys.version)"
    <python> -m pip --version
- Aucun Python >= 3.11 : STOP, en installer un (winget/brew/apt), relancer.

## 1. État du dépôt (clone, ou fast-forward sur place)
REPO = le répertoire cible.

### Cas A — REPO n'existe pas
  git clone --recurse-submodules <url> "$REPO"
  Si la forme one-shot échoue avec "not a git repository: /tmp/..." (quirk du
  helper submodule sous Git Bash, pas un problème du dépôt) :
  git clone <url> "$REPO" && git -C "$REPO" submodule update --init

### Cas B — REPO existe (le cas courant)
- N'utilise PAS `gh repo clone` sur un répertoire existant : la commande échoue
  avec "destination path already exists" et ne met rien à jour.
- `git -C "$REPO" status --porcelain` — si non vide, STOP et montre les fichiers.
  Pas de stash, pas de reset, pas de checkout.
- `git -C "$REPO" fetch --all --prune`
- `git -C "$REPO" merge --ff-only origin/main`
  Si le merge échoue (divergence) : STOP, ne réécris pas l'historique.
- Précondition : après le merge, `pyproject.toml` et `install.sh` DOIVENT exister
  à HEAD. Si l'un manque, le checkout est un commit de documentation seule —
  STOP et donne le hash du commit. N'essaie pas d'installer un paquet absent.
- Si `.gitmodules` existe et que `git -C "$REPO" submodule status` affiche un `-`
  en tête :
    git -C "$REPO" submodule update --init
  Pas de `--recursive` : .gitmodules ne déclare qu'un niveau,
  uhb/services/mari/framework. Le sous-module ne sert qu'à builder le bridge
  MARI ; le CLI et `uhb brain status` fonctionnent sans lui.

## 2. Installer avec le bon interpréteur
install.sh fait `PYTHON="${PYTHON:-python3}"`. Sous Windows, `python3` peut être
un venv de harness sans pip. Passe toujours PYTHON explicitement.

  Windows Git Bash :
    PYTHON="C:/Users/<user>/AppData/Local/Programs/Python/Python311/python.exe" \
      ./install.sh --with-yaml
  macOS / Linux :
    PYTHON=python3.11 ./install.sh --with-yaml

Options lues dans install.sh (une option inconnue sort en code 2 — n'en invente
pas) :
  --dry-run        affiche les commandes, ne change rien
  --with-yaml      installe aussi pyyaml, nécessaire à `uhb config set`
  --with-services  + SiYuan, téléchargé et vérifié par sha256 (~240 Mo)
  --full           + SiYuan + dépendances RAG (~1,5 Go) + build du bridge MARI
  -h, --help

Défaut pour une première installation : `--dry-run`, puis `--with-yaml`. Ne pas
utiliser `--full` sauf demande explicite de la stack complète.

install.sh crée `.venv` dans le dépôt, n'utilise jamais sudo, n'écrit rien hors du
checkout, et une deuxième exécution est une réinstallation sans effet de bord.
N'installe uhb-harness dans aucun autre environnement.

## 3. Vérifications (sortie brute, pas de paraphrase)
  .venv/Scripts/uhb.exe --version   (Windows)
  .venv/bin/uhb --version           (macOS / Linux)
  Attendu : UHB (Universal Harness Backend) 0.1.2 (ou supérieur)

  <uhb> status                 providers en loaded/MISSING, modes, état du dépôt
  <uhb> modes show review      "write": [] plus une liste deny
  <uhb> brain status           ● ou ○ par service, code 0 même si un service est
                               arrêté
  <uhb> docs                   chemin et URL du manuel embarqué

N'imprime jamais un identifiant : le rapport dit `loaded` ou `MISSING`, jamais une
valeur.

## 4. Rendre `uhb` disponible dans un shell neuf (ne PAS toucher au PATH sans accord)
Propose les deux options et attends le choix de l'utilisateur.

### Option A — wrapper (recommandé : non intrusif, réversible)
- Windows exige DEUX wrappers, un par famille de shell : `cmd.exe` et PowerShell
  exécutent un `.cmd`, Git Bash non — il lui faut un script sans extension avec un
  shebang. Les deux vont dans `%USERPROFILE%\.local\bin`, déjà dans le Path
  utilisateur et qui héberge déjà des wrappers :
      uhb.cmd -> @echo off
                 "<REPO>\.venv\Scripts\uhb.exe" %*
      uhb     -> #!/bin/sh
                 exec "/c/<REPO>/.venv/Scripts/uhb.exe" "$@"
  Que `where.exe uhb` trouve le `.cmd` ne prouve PAS que bash peut l'exécuter :
  bash répond alors "command not found". Tester les deux shells avant de conclure.
  Écrire `uhb.cmd` avec des fins de ligne CRLF, et jamais avec `printf` : printf
  consomme `\U` comme un échappement, donc le `\Users` du chemin est mangé et le
  fichier est écrit cassé. Utiliser un heredoc, ou écrire les octets — Python
  `Path(...).write_bytes(b'@echo off\r\n...')` — puis contrôler avec `od -c`.
  N'utilise PAS `%LOCALAPPDATA%\Microsoft\WindowsApps` : dossier d'alias UWP
  protégé, pas un endroit où déposer des scripts.
- macOS / Linux : lien symbolique dans ~/.local/bin (ne l'ajouter au Path que s'il
  est absent) :
      ln -s "$REPO/.venv/bin/uhb" "$HOME/.local/bin/uhb"

### Option B — ajouter au Path utilisateur (persistant)
- Windows : jamais `setx` (tronque à 1024 caractères). Lis uniquement la valeur
  utilisateur, et ajoute une seule fois :
      $venvScripts = "<REPO>\.venv\Scripts"
      $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
      if (($userPath -split ';') -notcontains $venvScripts) {
          [Environment]::SetEnvironmentVariable("Path", "$userPath;$venvScripts", "User")
      }
  Ne concatène PAS `$env:Path` : en session il vaut User + Machine, donc l'écrire
  recopie la moitié Machine dans la moitié User.
- macOS / Linux : ajoute au profil réellement utilisé (~/.zshrc ou ~/.bashrc).

Garde-fou PATH — relever, modifier, comparer. Ne présume aucun ordre :
- Avant : `where.exe hermes` et `where.exe python` (Windows) /
  `command -v hermes` et `command -v python` (Unix).
- Après : les mêmes commandes doivent renvoyer la même première entrée.
- Différent → STOP et montre les deux sorties.

## 5. Identifiants (seulement si de vrais appels modèle sont voulus)
UHB ne lit aucun fichier `.env`. La configuration est `uhb/config.yaml` à côté du
paquet, ou le chemin indiqué dans `$UHB_CONFIG` (uhb/core/router.py:17). Les clés
viennent de l'environnement du processus, sous les noms de variables déclarés dans
cette config — par exemple DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
KIMI_API_KEY. Ollama n'en demande aucune. Exporte la variable dans le shell qui
lance `uhb` ; n'affiche jamais une valeur.

## 6. Rapport final (concis, dans la langue de l'utilisateur)
- OS et interpréteur utilisés (chemin + version).
- HEAD avant / après, quand un fast-forward a eu lieu.
- Sortie de `uhb --version`.
- Providers : loaded / MISSING, sans valeurs.
- État du brain : rag / siyuan / mari, ● ou ○.
- Option PATH choisie (A ou B), et confirmation que `where.exe hermes` et
  `where.exe python` sont identiques à la référence relevée avant modification.
- Toute erreur, avec la commande exacte pour la reproduire.

## Règles absolues
- Pas de sudo, pas de setx.
- Pas de `git reset --hard`, pas de `--force`, pas de réécriture d'historique, pas
  de stash silencieux.
- N'installe uhb-harness dans aucun venv autre que `$REPO/.venv`.
- Aucun contournement improvisé (copie de DLL, second Python non validé, pip
  manuel dans un interpréteur système). Une étape qui échoue = STOP avec la sortie
  brute.
```

---

## Pourquoi chaque garde existe

| Garde | L'échec qu'elle évite |
| --- | --- |
| Précondition `pyproject.toml` + `install.sh` | installer depuis un commit de documentation seule — le blocage réellement observé sur un clone 8 commits en retard dont l'arbre ne contenait aucun `uhb/` |
| `PYTHON=` passé explicitement | `.venv` construit depuis un venv de harness au lieu de CPython 3.11+ |
| `merge --ff-only`, jamais `reset` | aligné sur `uhb update`, qui n'a ni `--force` ni `reset` |
| Wrapper avant édition du PATH | réordonner le Path change le `hermes` que le harness hôte résout |
| Deux wrappers sous Windows | `where.exe` trouve le `.cmd`, et bash répond quand même "command not found" |
| `GetEnvironmentVariable("Path","User")` | `$env:Path` vaut User + Machine : l'écrire recopie la moitié Machine |
| Relevé PATH avant/après | toute affirmation sur le dossier « qui doit rester premier » est propre à une machine et devient fausse |
| Aucun chargeur `.env` | le paquet n'en a pas ; un chemin de config inventé échoue au premier appel |

## Notes de l'exécution de référence

- Windows 11, Git Bash, `gh` authentifié. Les chemins passés à `-C` doivent
  s'écrire `C:/...` : git est un binaire natif et MSYS ne traduit pas les
  arguments.
- `python3` pointait vers un venv de harness ; l'interpréteur utilisable était
  `C:/Users/searc/AppData/Local/Programs/Python/Python311/python.exe` (3.11.9).
- Mesuré : 0.1.2 installé depuis `main@01c2436` ; `uhb status` a rapporté cinq
  providers et quatre modes ; `uhb brain status` a rapporté RAG, SiYuan et MARI
  répondants tous les trois, code 0 ; `uhb docs` est sorti en code 0 avec les deux
  PDF présents.
- Le clone est arrivé sur un commit de documentation seule (`6741380`) et a exigé
  un fast-forward de 104 fichiers / +13 179 lignes avant que `install.sh` existe
  seulement.
