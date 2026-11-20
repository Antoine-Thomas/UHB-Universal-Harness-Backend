"""Run lifecycle: what a run leaves behind, and for how long.

A run directory exists for one reason: the files a snippet produced have to outlive the
call, because an interface reads them afterwards. It has no reason to exist afterwards.
``execute_code`` therefore never deletes - it writes a ``manifest.json`` beside the code -
and this module owns the *policy*: close a run (purge, or archive it), and sweep the
leftovers of crashed processes.

Two rules that shape everything below:

* **Fail closed.** An archive that cannot be written means the run is **kept**, not
  deleted. Losing a run because git was missing is the failure mode this module refuses.
* **No escape.** Every deletion is checked against the real path of ``runs_root()``; a
  symlink pointing outside, a ``..`` in a name, an absolute path handed by a caller: all
  refused before anything is touched.

The archive is a bare repository (``~/.uhb/brain-runs.git`` by default): one commit per kept
run, a deterministic ``bundle.tar.gz``, the run's ``manifest.json``, and an ``index.jsonl``
that the second brain (``uhb brain``, the wiki, the RAG) can read without unpacking anything.
The repository is local and needs no service: SiYuan, the RAG and MARI can all be down and a
kept run is still archived, deterministically.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
INDEX = "index.jsonl"
DEFAULT_ARCHIVE = "~/.uhb/brain-runs.git"
#: Files a run always carries and that never belong in an archive of "what it produced".
_FURNITURE = {"code.py", "_uhb_runner.py", MANIFEST, ".mpl"}


class RunsError(RuntimeError):
    """A refusal the caller can show: nothing was deleted, nothing was lost."""


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunsPolicy:
    """Retention, in one object, so a test can state it instead of mocking it."""

    purge_default: bool = True
    ttl_days: float = 7.0
    max_dirs: int = 200
    max_bytes: int = 500 * 1024 * 1024
    archive_repo: str = DEFAULT_ARCHIVE
    keep_index: int = 2000  # lines kept in index.jsonl before rotation

    @property
    def repo_path(self) -> Path:
        return Path(self.archive_repo).expanduser()

    @classmethod
    def from_env(cls, base: RunsPolicy | None = None) -> RunsPolicy:
        """Defaults, overridden by ``uhb/config.yaml`` (``runs.*``), then by the environment."""
        current = base or cls()
        section = _read_config().get("runs")
        config: dict[str, Any] = section if isinstance(section, dict) else {}

        def pick(key: str, default: Any, cast: Any) -> Any:
            if key not in config:
                return default
            try:
                return cast(config[key])
            except (TypeError, ValueError):
                return default

        def flag(value: Any) -> bool:
            return str(value).strip().lower() not in {"0", "false", "no", "off"}

        def number(cast: Any) -> Any:
            return lambda value: cast(float(str(value)))

        purge = pick("purge_default", current.purge_default, flag)
        ttl = pick("ttl_days", current.ttl_days, number(float))
        dirs = pick("max_dirs", current.max_dirs, number(int))
        size = pick("max_bytes", current.max_bytes, number(int))
        repo = pick("archive_repo", current.archive_repo, str)

        env = os.environ
        if env.get("UHB_RUNS_PURGE_DEFAULT"):
            purge = flag(env["UHB_RUNS_PURGE_DEFAULT"])
        for variable, name in (
            ("UHB_RUNS_TTL_DAYS", "ttl"),
            ("UHB_RUNS_MAX_DIRS", "dirs"),
            ("UHB_RUNS_MAX_BYTES", "size"),
        ):
            raw = env.get(variable, "").strip()
            if raw:
                try:
                    parsed = float(raw)
                except ValueError:
                    continue
                if name == "ttl":
                    ttl = parsed
                elif name == "dirs":
                    dirs = int(parsed)
                else:
                    size = int(parsed)
        if env.get("UHB_RUNS_ARCHIVE"):
            repo = env["UHB_RUNS_ARCHIVE"]

        return cls(
            purge_default=bool(purge),
            ttl_days=float(ttl),
            max_dirs=int(dirs),
            max_bytes=int(size),
            archive_repo=str(repo),
            keep_index=current.keep_index,
        )


def _read_config() -> dict[str, Any]:
    candidates = [Path(os.environ.get("UHB_CONFIG") or "")] if os.environ.get("UHB_CONFIG") else []
    candidates.append(Path(__file__).resolve().parent / "config.yaml")
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                import yaml  # local import: no hard dependency

                data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                return data if isinstance(data, dict) else {}
            except Exception:  # noqa: BLE001 - a broken config must not break the policy
                return {}
    return {}


# --------------------------------------------------------------------------- #
# reading a run
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunInfo:
    path: Path
    run_id: str
    size: int
    mtime: float
    keep: bool = False
    archived: bool = False
    mode: str = ""
    files: tuple[str, ...] = ()

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.mtime) / 86400.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run_id,
            "path": str(self.path),
            "bytes": self.size,
            "age_days": round(self.age_days, 2),
            "keep": self.keep,
            "archived": self.archived,
            "mode": self.mode,
            "files": list(self.files),
        }


def runs_root() -> Path:
    from .tools.execute import runs_root as _root

    return _root()


def _inside(root: Path, candidate: Path) -> bool:
    """True only if ``candidate``'s real path is under ``root``'s real path."""
    try:
        real_root = Path(os.path.realpath(root))
        real = Path(os.path.realpath(candidate))
        real.relative_to(real_root)
    except (OSError, ValueError):
        return False
    return real != real_root


def _tree_bytes(folder: Path) -> int:
    total = 0
    for item in folder.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def read_manifest(folder: Path) -> dict[str, Any]:
    target = Path(folder) / MANIFEST
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(folder: Path, *, mode: str = "", keep: bool = False, **extra: Any) -> Path:
    """Record what this run is, so a later policy can decide without guessing."""
    folder = Path(folder)
    data = {
        "run": folder.name,
        "tool": "execute_code",
        "mode": mode,
        "keep": bool(keep),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **{key: value for key, value in extra.items() if value is not None},
    }
    target = folder / MANIFEST
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def list_runs(*, root: Path | None = None, limit: int = 0) -> list[RunInfo]:
    base = Path(root) if root else runs_root()
    if not base.is_dir():
        return []
    found: list[RunInfo] = []
    for folder in base.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        manifest = read_manifest(folder)
        try:
            stat = folder.stat()
        except OSError:
            continue
        files = tuple(
            sorted(
                str(item.relative_to(folder)).replace("\\", "/")
                for item in folder.rglob("*")
                if item.is_file() and item.name not in _FURNITURE
            )
        )
        found.append(
            RunInfo(
                path=folder,
                run_id=folder.name,
                size=_tree_bytes(folder),
                mtime=stat.st_mtime,
                keep=bool(manifest.get("keep")),
                archived=bool(manifest.get("archived")),
                mode=str(manifest.get("mode") or ""),
                files=files,
            )
        )
    found.sort(key=lambda run: run.mtime, reverse=True)
    return found[:limit] if limit else found


def _resolve(run: str | Path, *, root: Path | None = None) -> Path:
    base = Path(root) if root else runs_root()
    candidate = Path(run)
    if not candidate.is_absolute():
        candidate = base / candidate
    if not _inside(base, candidate):
        raise RunsError(f"refusé : {candidate} n'est pas sous {base}")
    if not candidate.is_dir():
        raise RunsError(f"run introuvable : {candidate.name}")
    return candidate


# --------------------------------------------------------------------------- #
# closing a run
# --------------------------------------------------------------------------- #
def close(
    run: str | Path,
    *,
    keep: bool | None = None,
    policy: RunsPolicy | None = None,
    root: Path | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """Decide the fate of one finished run: archived if kept, deleted otherwise."""
    active = policy or RunsPolicy.from_env()
    folder = _resolve(run, root=root)
    manifest = read_manifest(folder)
    wanted = bool(manifest.get("keep")) if keep is None else bool(keep)

    if wanted:
        record = archive(folder, policy=active, summary=summary)
        return {"action": "archived", **record}

    if not active.purge_default and keep is None:
        return {"action": "kept", "run": folder.name, "reason": "purge disabled by policy"}

    removed = _purge(folder, keep_marked=False)
    return {"action": "purged", "run": folder.name, "removed_bytes": removed}


def _purge(folder: Path, *, keep_marked: bool) -> int:
    size = _tree_bytes(folder)
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        raise RunsError(f"purge impossible de {folder.name} : {exc}") from exc
    return size


# --------------------------------------------------------------------------- #
# the « Cerveaux » archive
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str, work_tree: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_DIR"] = str(repo)
    env.pop("GIT_WORK_TREE", None)
    if work_tree is not None:
        env["GIT_WORK_TREE"] = str(work_tree)
    proc = subprocess.run(
        ("git", *args),
        capture_output=True,
        text=True,
        # git rend de l'UTF-8 et index.jsonl est ecrit en UTF-8 (ligne plus bas). Sans ceci,
        # `text=True` decode avec l'encodage local : cp1252 sur un runner Windows, et tout
        # resume accentue revient corrompu. Mesure : utf8_mode=1 masque le defaut en local,
        # la CI tombe sur les quatre versions Windows.
        encoding="utf-8",
        env=env,
        cwd=str(work_tree or repo),
        check=False,
    )
    if check and proc.returncode != 0:
        raise RunsError(f"git {args[0]} a échoué : {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc


def ensure_repo(policy: RunsPolicy | None = None) -> Path:
    active = policy or RunsPolicy.from_env()
    repo = active.repo_path
    if (repo / "HEAD").is_file():
        return repo
    repo.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(repo)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise RunsError(f"dépôt Cerveaux impossible à créer : {(proc.stderr or proc.stdout).strip()[:300]}")
    return repo


def _bundle(folder: Path, target: Path) -> str:
    """A deterministic tar.gz: sorted entries, zeroed mtimes, no owner names.

    Determinism is not decoration: two archives of the same run must hash the same, or
    ``restore`` cannot prove it gave the run back.
    """
    digest = hashlib.sha256()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for item in sorted(folder.rglob("*"), key=lambda path: str(path).replace("\\", "/")):
            if not item.is_file():
                continue
            relative = str(item.relative_to(folder)).replace("\\", "/")
            if relative.split("/")[0] == ".mpl":
                continue
            if relative == MANIFEST:
                # The manifest travels beside the bundle, not inside it: it carries a
                # timestamp, and a bundle that hashed differently every second would make
                # the digest useless as an identity.
                continue
            data = item.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    import gzip

    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    target.write_bytes(compressed)
    digest.update(compressed)
    return digest.hexdigest()


def archive(
    run: str | Path,
    *,
    policy: RunsPolicy | None = None,
    root: Path | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """Compress a run and commit it into the Cerveaux repository. Fails closed."""
    active = policy or RunsPolicy.from_env()
    folder = _resolve(run, root=root)
    repo = ensure_repo(active)
    manifest = read_manifest(folder)

    with tempfile.TemporaryDirectory(prefix="uhb-archive-") as raw_tmp:
        work = Path(raw_tmp)
        target_dir = work / "runs" / folder.name
        target_dir.mkdir(parents=True)
        bundle = target_dir / "bundle.tar.gz"
        sha = _bundle(folder, bundle)
        entry = {
            **manifest,
            "run": folder.name,
            "archived": True,
            "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "bundle": f"runs/{folder.name}/bundle.tar.gz",
            "sha256": sha,
            "bytes": bundle.stat().st_size,
            "source_bytes": _tree_bytes(folder),
            "summary": (summary or manifest.get("summary") or "")[:400],
        }
        (target_dir / MANIFEST).write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        index = work / INDEX
        line = json.dumps(
            {
                "run": entry["run"],
                "sha256": sha,
                "mode": entry.get("mode", ""),
                "archived_at": entry["archived_at"],
                "bytes": entry["bytes"],
                "summary": entry["summary"],
                "session": entry.get("session", ""),
            },
            ensure_ascii=False,
        )
        index.write_text(line + "\n", encoding="utf-8")

        try:
            _git(repo, "-c", "core.bare=false", "add", "-A", ".", work_tree=work)
            staged = _git(repo, "-c", "core.bare=false", "status", "--porcelain", work_tree=work)
            if staged.stdout.strip():
                _git(
                    repo,
                    "-c", "core.bare=false", "-c", "user.name=uhb", "-c", "user.email=uhb@localhost",
                    "commit",
                    "-m", f"keep {entry['run']} (mode={entry.get('mode') or 'n/a'}, "
                          f"{len(manifest.get('files') or [])} files, {entry['bytes']} B)",
                    # The bundle digest is in the commit, so `restore` can be asked for the
                    # hash the manifest and the index publish, not only a git revision.
                    "-m", f"Bundle-SHA256: {sha}",
                    work_tree=work,
                )
            # Identical content archives to identical bytes: the repository already holds
            # them, so there is nothing to commit and that is not a failure.
            head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        except RunsError as exc:
            # Fail closed: the run stays on disk, exactly as it was.
            raise RunsError(f"archivage impossible, le run est conservé : {exc}") from exc

    write_manifest(folder, mode=str(manifest.get("mode") or ""), keep=True,
                   archived=True, archive_sha=sha, archive_commit=head)
    _drop(folder)
    return {"run": entry["run"], "sha256": sha, "commit": head, "bundle": entry["bundle"],
            "bytes": entry["bytes"], "source_bytes": entry["source_bytes"], "repo": str(repo)}


def _drop(folder: Path) -> None:
    """The archive holds the run now; the live copy goes."""
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        raise RunsError(f"run archivé mais impossible à purger : {exc}") from exc


def history(*, policy: RunsPolicy | None = None, limit: int = 20) -> list[dict[str, Any]]:
    active = policy or RunsPolicy.from_env()
    repo = active.repo_path
    if not (repo / "HEAD").is_file():
        return []
    proc = _git(repo, "log", f"-{max(1, limit)}", "--pretty=%H%x09%ad%x09%s", "--date=short", check=False)
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append({"commit": parts[0], "date": parts[1], "message": parts[2]})
    return rows


def restore(sha: str, *, policy: RunsPolicy | None = None) -> Path:
    """Give a run back, byte for byte, and prove it with the manifest's SHA-256."""
    active = policy or RunsPolicy.from_env()
    repo = active.repo_path
    if not (repo / "HEAD").is_file():
        raise RunsError("aucun dépôt Cerveaux : rien à restaurer")
    if not sha or len(sha) < 7 or any(char not in "0123456789abcdef" for char in sha.lower()):
        raise RunsError(f"révision invalide : {sha!r}")
    commit = _git(repo, "rev-parse", f"{sha}^{{commit}}", check=False)
    if commit.returncode != 0:
        # Not a git revision: try the bundle digest the manifest and the index publish.
        # `archive` writes it as a `Bundle-SHA256:` trailer precisely so this works.
        lookup = _git(repo, "log", "--all", "--format=%H", "--grep", f"Bundle-SHA256: {sha}",
                      check=False)
        found = lookup.stdout.split()[:1]
        if not found:
            raise RunsError(f"révision ou empreinte inconnue : {sha}")
        commit = _git(repo, "rev-parse", f"{found[0]}^{{commit}}", check=False)
        if commit.returncode != 0:
            raise RunsError(f"révision inconnue : {sha}")
    resolved = commit.stdout.strip()

    listing = _git(repo, "ls-tree", "-r", "--name-only", resolved).stdout.split()
    bundles = [name for name in listing if name.endswith("bundle.tar.gz")]
    if not bundles:
        raise RunsError(f"{resolved[:7]} ne contient aucun bundle")
    bundle = bundles[0]
    manifest_path = bundle.rsplit("/", 1)[0] + "/" + MANIFEST
    if manifest_path not in listing:
        raise RunsError(f"{resolved[:7]} n'a pas de manifeste")
    entry = json.loads(_git(repo, "cat-file", "-p", f"{resolved}:{manifest_path}").stdout)
    payload = _git_binary(repo, f"{resolved}:{bundle}")
    if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
        raise RunsError("le bundle ne correspond pas à l'empreinte du manifeste")

    root = runs_root()
    destination = root / str(entry.get("run") or "run-restored")
    if destination.exists():
        raise RunsError(f"{destination.name} existe déjà : restauration refusée (aucun écrasement)")
    destination.mkdir(parents=True)
    # Aucun `extractall`, sur aucune version : l'extraction est faite membre par membre,
    # validée ici. `filter="data"` n'existe pas en 3.10 et laissait la CI rouge ; surtout, une
    # détection de compatibilité aurait produit deux chemins de code dont un seul est testé.
    _extract_bundle(payload, destination)
    write_manifest(destination, mode=str(entry.get("mode") or ""), keep=True,
                   archived=True, archive_sha=entry.get("sha256"), archive_commit=resolved)
    return destination


#: Un nom de membre refusé avant toute écriture : absolu POSIX, lecteur Windows (`C:`),
#: UNC (`\\serveur\partage`), ou remontée par `..`.
_DRIVE = re.compile(r"^[A-Za-z]:")


def _member_parts(name: str) -> list[str]:
    """Composants sûrs d'un nom de membre, ou refus. Aucune écriture n'a encore eu lieu."""
    raw = name.replace("\\", "/")
    if not raw or raw.startswith(("/", "//")):
        raise RunsError(f"entrée refusée dans l'archive : {name!r}")
    if _DRIVE.match(raw):
        raise RunsError(f"entrée refusée dans l'archive (lecteur) : {name!r}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise RunsError(f"entrée refusée dans l'archive : {name!r}")
    return parts


def _ensure_folder(destination: Path, parts: list[str]) -> None:
    """Crée les dossiers parents un à un, sans jamais traverser un lien symbolique."""
    current = destination
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RunsError(f"dossier parent symbolique refusé : {part!r}")
        if current.exists():
            if not current.is_dir():
                raise RunsError(f"un fichier occupe la place du dossier {part!r}")
            continue
        current.mkdir()


def _extract_bundle(payload: bytes, destination: Path) -> int:
    """Restaure l'archive dans ``destination``, membre par membre. Rend le nombre de fichiers.

    Règles, identiques sur toutes les versions de Python :

    * seuls les **fichiers réguliers** sont extraits : lien symbolique, lien dur, dossier,
      périphérique et FIFO sont refusés ;
    * nom validé (absolu, lecteur, UNC, ``..``) **avant** la moindre écriture ;
    * les dossiers parents sont créés un à un et un lien symbolique préexistant est refusé —
      c'est ce qui empêche une sortie par un lien déjà présent dans le dossier ;
    * aucune métadonnée de l'archive n'est appliquée : ni permissions, ni propriétaire, ni
      mtime, ni attributs. Les octets sont écrits, rien d'autre ;
    * une cible déjà présente est refusée : aucune restauration n'écrase un fichier.
    """
    try:
        # Un seul gestionnaire de contexte, et l'erreur d'ouverture traduite en refus lisible :
        # gzip leve EOFError sur une archive tronquee, tarfile leve ReadError sur autre chose.
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            written = 0
            for member in archive.getmembers():
                if not member.isfile():
                    raise RunsError(
                        "entrée refusée dans l'archive (pas un fichier régulier) : "
                        f"{member.name!r}"
                    )
                parts = _member_parts(member.name)
                target = destination.joinpath(*parts)
                _ensure_folder(destination, parts[:-1])
                if target.exists() or target.is_symlink():
                    raise RunsError(f"restauration refusée, {target.name} existe déjà")
                handle = archive.extractfile(member)
                if handle is None:
                    raise RunsError(f"contenu illisible pour {member.name!r}")
                try:
                    with handle, open(target, "xb") as out:
                        shutil.copyfileobj(handle, out)
                except OSError as exc:
                    # Un disque plein ou un droit refuse n'est PAS une archive illisible :
                    # le message doit dire ce qui s'est passe, pas accuser l'archive.
                    # Et le fichier partiel ne doit pas rester : un run a moitie restaure
                    # serait pire qu'un refus.
                    target.unlink(missing_ok=True)
                    raise RunsError(
                        f"écriture impossible : {target.name} ({exc.strerror or exc})"
                    ) from exc
                written += 1
            return written
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise RunsError(f"archive illisible ou tronquée : {type(exc).__name__}") from exc


def _git_binary(repo: Path, spec: str) -> bytes:
    env = dict(os.environ)
    env["GIT_DIR"] = str(repo)
    proc = subprocess.run(("git", "cat-file", "-p", spec), capture_output=True, env=env, cwd=str(repo), check=False)
    if proc.returncode != 0:
        raise RunsError(f"bundle illisible : {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


# --------------------------------------------------------------------------- #
# the safety net
# --------------------------------------------------------------------------- #
def prune(
    *,
    policy: RunsPolicy | None = None,
    root: Path | None = None,
    dry_run: bool = True,
    older_than: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """TTL then quota. A kept or archived run is never a candidate."""
    active = policy or RunsPolicy.from_env()
    base = Path(root) if root else runs_root()
    runs = list_runs(root=base)
    ttl = active.ttl_days if older_than is None else older_than

    protected = [run for run in runs if run.keep or run.archived]
    candidates = [run for run in runs if not (run.keep or run.archived)]

    expired = [run for run in candidates if run.age_days > ttl]
    remaining = [run for run in candidates if run.age_days <= ttl]

    over_dirs = max(0, len(remaining) - active.max_dirs)
    total_bytes = sum(run.size for run in remaining)
    over_age: list[RunInfo] = []
    for run in sorted(remaining, key=lambda item: item.mtime):  # oldest first
        if over_dirs > 0 or total_bytes > active.max_bytes:
            over_age.append(run)
            over_dirs -= 1
            total_bytes -= run.size
        else:
            break

    doomed = {run.run_id: run for run in expired + over_age}
    freed = sum(run.size for run in doomed.values())
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "runs": len(runs),
        "protected": len(protected),
        "expired": [run.run_id for run in expired],
        "over_quota": [run.run_id for run in over_age],
        "candidates": len(doomed),
        "freed_bytes": freed,
        "policy": {
            "ttl_days": ttl,
            "max_dirs": active.max_dirs,
            "max_bytes": active.max_bytes,
            "purge_default": active.purge_default,
            "archive_repo": str(active.repo_path),
        },
    }
    if dry_run:
        return report

    removed = 0
    for run in doomed.values():
        try:
            _purge(_resolve(run.path, root=base), keep_marked=False)
            removed += 1
        except RunsError:
            continue
    report["removed"] = removed
    return report


def status(*, policy: RunsPolicy | None = None, limit: int = 20) -> dict[str, Any]:
    active = policy or RunsPolicy.from_env()
    runs = list_runs(limit=limit)
    return {
        "root": str(runs_root()),
        "count": len(list_runs()),
        "bytes": sum(run.size for run in list_runs()),
        "kept": [run.to_dict() for run in runs if run.keep],
        "recent": [run.to_dict() for run in runs[:limit]],
        "policy": {
            "purge_default": active.purge_default,
            "ttl_days": active.ttl_days,
            "max_dirs": active.max_dirs,
            "max_bytes": active.max_bytes,
            "archive_repo": str(active.repo_path),
            "archive_exists": (active.repo_path / "HEAD").is_file(),
        },
    }


__all__ = [
    "DEFAULT_ARCHIVE",
    "INDEX",
    "MANIFEST",
    "RunInfo",
    "RunsError",
    "RunsPolicy",
    "archive",
    "close",
    "ensure_repo",
    "history",
    "list_runs",
    "prune",
    "read_manifest",
    "restore",
    "runs_root",
    "status",
    "write_manifest",
]
