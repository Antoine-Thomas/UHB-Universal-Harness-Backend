"""P3-16a: a run lives until it is closed, and nothing piles up in between.

The three proofs the roadmap asks for (Gate D), plus the two refusals that make them
trustworthy:

* **D-1** a run closed without ``keep`` leaves nothing on disk;
* **D-2** a kept run is compressed into the Cerveaux repository, one commit, restorable
  byte for byte by its SHA-256;
* **D-3** the TTL/quota sweep removes leftovers and never touches a kept run;
* **fail closed** an archive that cannot be written keeps the run;
* **no escape** a path outside the runs root is refused before anything is deleted.

And the regression that matters most: ``execute_code`` itself still deletes **nothing** -
an interface reads the images after the call returns.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from uhb import runs
from uhb.tools import execute


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setenv(execute.RUNS_ENV, str(runs_dir))
    monkeypatch.setenv("UHB_RUNS_ARCHIVE", str(tmp_path / "brain-runs.git"))
    return runs_dir


@pytest.fixture()
def policy(tmp_path: Path) -> runs.RunsPolicy:
    return runs.RunsPolicy(archive_repo=str(tmp_path / "brain-runs.git"), ttl_days=7.0, max_dirs=200)


def _run(root: Path, name: str, *, keep: bool = False, files: dict[str, str] | None = None,
         age_days: float = 0.0, mode: str = "dev") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "code.py").write_text("print(1)\n", encoding="utf-8")
    for relative, content in (files or {"plot.png": "PNG-ish bytes"}).items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    runs.write_manifest(folder, mode=mode, keep=keep, session="s1")
    if age_days:
        stamp = time.time() - age_days * 86400
        for item in folder.rglob("*"):
            os.utime(item, (stamp, stamp))
        os.utime(folder, (stamp, stamp))
    return folder


# --------------------------------------------------------------------------- #
# D-1 : nothing left behind
# --------------------------------------------------------------------------- #
def test_a_run_closed_without_keep_leaves_nothing(root: Path, policy: runs.RunsPolicy) -> None:
    folder = _run(root, "run-20260101-000000-aaaaaa")

    result = runs.close(folder, keep=False, policy=policy, root=root)

    assert result["action"] == "purged"
    assert not folder.exists()
    assert list(root.iterdir()) == []


def test_a_run_is_described_before_it_runs_so_a_kill_is_recoverable(root: Path) -> None:
    folder = _run(root, "run-20260101-000000-bbbbbb", keep=True)
    manifest = json.loads((folder / runs.MANIFEST).read_text(encoding="utf-8"))

    assert manifest["keep"] is True and manifest["mode"] == "dev"
    assert manifest["run"] == folder.name


def test_the_manifest_is_written_by_execute_code_before_the_snippet(
    root: Path, policy: runs.RunsPolicy
) -> None:
    """The one regression that would break the interface: a run deleted too early."""
    result = execute.execute_code("print('bonjour')", mode="dev", keep=False)

    assert result.run_id and result.run_dir.is_dir(), "les images doivent survivre à l'appel"
    manifest = runs.read_manifest(result.run_dir)
    assert manifest["keep"] is False and manifest["code_sha256"]
    assert "run" in result.to_dict() and result.to_dict()["keep"] is False


# --------------------------------------------------------------------------- #
# D-2 : the Cerveaux archive
# --------------------------------------------------------------------------- #
def test_a_kept_run_is_archived_and_restorable_byte_for_byte(
    root: Path, policy: runs.RunsPolicy
) -> None:
    folder = _run(root, "run-20260102-000000-cccccc", keep=True,
                  files={"plot.png": "PNG-ish bytes", "data/out.csv": "a,b\n1,2\n"})

    record = runs.close(folder, keep=True, policy=policy, root=root, summary="a chart")

    assert record["action"] == "archived"
    assert not folder.exists(), "l'archive remplace le run, elle ne le double pas"
    assert record["sha256"] and record["commit"]

    log = subprocess.run(["git", "--git-dir", str(policy.repo_path), "log", "--oneline"],
                         capture_output=True, text=True, check=False)
    assert log.returncode == 0 and "keep run-20260102-000000-cccccc" in log.stdout

    restored = runs.restore(record["sha256"][:12], policy=policy)

    assert restored.name == folder.name
    assert (restored / "plot.png").read_text(encoding="utf-8") == "PNG-ish bytes"
    assert (restored / "data" / "out.csv").read_text(encoding="utf-8") == "a,b\n1,2\n"
    manifest = runs.read_manifest(restored)
    assert manifest["archived"] is True and manifest["archive_sha"] == record["sha256"]


def test_two_archives_of_the_same_run_have_the_same_bytes(root: Path, policy: runs.RunsPolicy) -> None:
    """Determinism is what lets the manifest's SHA-256 mean something.

    Two archives of identical content must hash identically, whatever their path or
    timestamp: that is the property `restore` relies on when it compares digests.
    """
    folder = _run(root, "run-20260103-000000-dddddd", keep=True)
    first = runs.archive(folder, policy=policy, root=root, summary="")

    again = _run(root, "run-20260103-000000-dddddd", keep=True)
    second = runs.archive(again, policy=policy, root=root, summary="")

    assert first["sha256"] == second["sha256"]
    assert len(first["sha256"]) == 64


def test_the_index_names_every_kept_run(root: Path, policy: runs.RunsPolicy) -> None:
    folder = _run(root, "run-20260104-000000-eeeeee", keep=True)
    record = runs.close(folder, keep=True, policy=policy, root=root, summary="un résumé")

    listing = subprocess.run(
        ["git", "--git-dir", str(policy.repo_path), "show", "HEAD:index.jsonl"],
        capture_output=True, text=True, check=False,
    )
    assert listing.returncode == 0
    entry = json.loads(listing.stdout.splitlines()[-1])
    assert entry["run"] == folder.name and entry["sha256"] == record["sha256"]
    assert entry["summary"] == "un résumé" and entry["mode"] == "dev"


def test_an_archive_that_cannot_be_written_keeps_the_run(
    root: Path, tmp_path: Path
) -> None:
    """Fail closed: a missing git or an unwritable repo never costs a run."""
    broken = tmp_path / "not-a-repo" / "file.txt"
    broken.parent.mkdir()
    broken.write_text("x", encoding="utf-8")
    bad = runs.RunsPolicy(archive_repo=str(broken))
    folder = _run(root, "run-20260105-000000-ffffff", keep=True)

    with pytest.raises(runs.RunsError):
        runs.close(folder, keep=True, policy=bad, root=root)

    assert folder.is_dir(), "le run doit survivre à un archivage impossible"
    assert (folder / "plot.png").is_file()


# --------------------------------------------------------------------------- #
# D-3 : the safety net
# --------------------------------------------------------------------------- #
def test_prune_expires_by_ttl_and_spares_kept_runs(root: Path, policy: runs.RunsPolicy) -> None:
    old = _run(root, "run-old-111111", age_days=30)
    fresh = _run(root, "run-fresh-222222", age_days=1)
    kept = _run(root, "run-kept-333333", keep=True, age_days=60)

    report = runs.prune(policy=policy, root=root, dry_run=False)

    assert report["expired"] == ["run-old-111111"]
    assert not old.exists() and fresh.is_dir()
    assert kept.is_dir(), "un run gardé n'est jamais un candidat"
    assert report["removed"] == 1


def test_prune_enforces_the_directory_quota_oldest_first(root: Path) -> None:
    tight = runs.RunsPolicy(archive_repo=str(root.parent / "brain.git"), ttl_days=3650, max_dirs=2)
    for index, age in enumerate((5, 4, 3, 2, 1)):
        _run(root, f"run-q-{index}-{index}{index}{index}{index}{index}{index}", age_days=age)
    kept = _run(root, "run-q-kept", keep=True, age_days=9)

    report = runs.prune(policy=tight, root=root, dry_run=False)

    assert len(report["over_quota"]) == 3
    assert report["removed"] == 3
    survivors = sorted(item.name for item in root.iterdir())
    assert kept.name in survivors and len(survivors) == 3


def test_prune_enforces_the_byte_quota(root: Path, tmp_path: Path) -> None:
    big = runs.RunsPolicy(archive_repo=str(tmp_path / "brain.git"), ttl_days=3650,
                          max_dirs=1000, max_bytes=120)
    for index, age in enumerate((3, 2, 1)):
        _run(root, f"run-b-{index}-{index}{index}{index}{index}{index}{index}",
             files={"blob.bin": "x" * 60}, age_days=age)

    report = runs.prune(policy=big, root=root, dry_run=False)

    assert report["candidates"] >= 1
    assert sum(item.stat().st_size for item in root.rglob("*") if item.is_file()) <= 200


def test_a_dry_run_touches_nothing(root: Path, policy: runs.RunsPolicy) -> None:
    _run(root, "run-dry-444444", age_days=30)

    report = runs.prune(policy=policy, root=root, dry_run=True)

    assert report["dry_run"] is True and report["candidates"] == 1
    assert (root / "run-dry-444444").is_dir()


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_path_outside_the_runs_root_is_refused(root: Path, policy: runs.RunsPolicy) -> None:
    outside = root.parent / "ailleurs"
    outside.mkdir()

    with pytest.raises(runs.RunsError):
        runs.close("../ailleurs", policy=policy, root=root)
    with pytest.raises(runs.RunsError):
        runs.close(outside, policy=policy, root=root)

    assert outside.is_dir(), "un refus ne supprime rien"


def test_status_reports_what_it_weighs(root: Path, policy: runs.RunsPolicy) -> None:
    _run(root, "run-s-555555", files={"big.bin": "x" * 400})
    data = runs.status(policy=policy)

    assert data["count"] == 1 and data["bytes"] >= 400
    assert data["policy"]["archive_exists"] is False
