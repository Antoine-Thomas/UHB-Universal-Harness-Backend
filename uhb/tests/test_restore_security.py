"""Restauration d'archive : membre par membre, sans `extractall`, sur toutes les versions.

Ces tests vérifient des **effets sur le système de fichiers**, pas des messages : après une
tentative malveillante, on regarde ce qui existe réellement, y compris hors du dossier de
destination. C'est la leçon de la CI rouge en 3.10 : un test qui ne lit qu'une chaîne aurait
dit « refusé » sans prouver qu'aucun fichier n'a été écrit.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from uhb import runs


def bundle(members: list[tuple[str, bytes, str]]) -> bytes:
    """Construit un tar.gz. ``kind`` vaut ``file``, ``dir``, ``sym``, ``hard`` ou ``fifo``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = "cible-ailleurs"
                tar.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = "cible-interne"
                tar.addfile(info)
            else:
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
    return buffer.getvalue()


def extract(payload: bytes, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    return runs._extract_bundle(payload, destination)


def test_a_normal_archive_comes_back_byte_for_byte(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    payload = bundle([("plot.png", b"\x89PNG contenu", "file"), ("data/out.csv", b"a,b\n1,2\n", "file")])

    written = extract(payload, destination)

    assert written == 2
    assert (destination / "plot.png").read_bytes() == b"\x89PNG contenu"
    assert (destination / "data" / "out.csv").read_bytes() == b"a,b\n1,2\n"


def test_a_parent_traversal_is_refused_and_nothing_lands_outside(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    payload = bundle([("../evade.txt", b"x", "file")])

    with pytest.raises(runs.RunsError):
        extract(payload, destination)

    assert not (tmp_path / "evade.txt").exists(), "aucun fichier hors de la destination"
    assert list(destination.rglob("*")) == []


def test_an_absolute_path_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    with pytest.raises(runs.RunsError):
        extract(bundle([("/etc/passwd", b"x", "file")]), destination)
    assert list(destination.rglob("*")) == []


def test_a_windows_drive_path_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    with pytest.raises(runs.RunsError):
        extract(bundle([("C:/Windows/evil.txt", b"x", "file")]), destination)
    assert list(destination.rglob("*")) == []


def test_a_unc_path_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    with pytest.raises(runs.RunsError):
        extract(bundle([("\\\\serveur\\partage\\evil.txt", b"x", "file")]), destination)
    assert list(destination.rglob("*")) == []


@pytest.mark.parametrize("kind", ["dir", "sym", "hard", "fifo"])
def test_anything_but_a_regular_file_is_refused(tmp_path: Path, kind: str) -> None:
    destination = tmp_path / "run"
    with pytest.raises(runs.RunsError):
        extract(bundle([("entree", b"", kind)]), destination)
    assert list(destination.rglob("*")) == []


def test_a_symbolic_folder_already_present_is_not_followed(tmp_path: Path) -> None:
    """La sortie par un lien déjà en place : le dossier parent ne doit jamais être suivi."""
    destination = tmp_path / "run"
    destination.mkdir()
    outside = tmp_path / "ailleurs"
    outside.mkdir()
    link = destination / "data"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("création de lien symbolique non autorisée sur ce poste")

    with pytest.raises(runs.RunsError):
        extract(bundle([("data/out.csv", b"a,b\n", "file")]), destination)

    assert list(outside.rglob("*")) == [], "rien ne doit atterrir derrière le lien"


def test_an_existing_target_is_never_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    destination.mkdir()
    (destination / "plot.png").write_bytes(b"ancien")

    with pytest.raises(runs.RunsError):
        extract(bundle([("plot.png", b"nouveau", "file")]), destination)

    assert (destination / "plot.png").read_bytes() == b"ancien"


def test_a_truncated_archive_is_a_readable_refusal(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    payload = bundle([("plot.png", b"x" * 500, "file")])[:-40]

    with pytest.raises(runs.RunsError):
        extract(payload, destination)

    assert list(destination.rglob("*")) == []


def test_a_non_archive_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    with pytest.raises(runs.RunsError):
        extract(b"ceci n'est pas un tar", destination)


def test_the_archive_metadata_is_not_applied(tmp_path: Path) -> None:
    """Aucune permission, propriétaire ni mtime de l'archive n'est appliqué."""
    destination = tmp_path / "run"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("script.sh")
        info.size = 4
        info.mode = 0o777
        info.mtime = 0
        info.uid = 4321
        info.gid = 4321
        info.uname = "quelquun"
        tar.addfile(info, io.BytesIO(b"echo"))

    extract(buffer.getvalue(), destination)

    restored = destination / "script.sh"
    assert restored.stat().st_mode & 0o777 != 0o777, "le bit exécutable de l'archive n'est pas repris"
    assert restored.stat().st_mtime > 0, "le mtime à zéro de l'archive n'est pas repris"


def test_no_call_to_extractall_survives() -> None:
    """La garde lit le CODE, pas les commentaires qui expliquent pourquoi il n'y est plus."""
    lines = Path(runs.__file__).read_text(encoding="utf-8").splitlines()
    code = [line.split("#", 1)[0] for line in lines]
    assert not any("extractall" in line for line in code), (
        "l'extraction doit rester membre par membre"
    )
    assert not any('filter="data"' in line for line in code)


def test_a_write_failure_is_not_reported_as_a_broken_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disque plein : le message dit l'écriture, pas l'archive — et rien de partiel ne reste."""
    destination = tmp_path / "run"
    payload = bundle([("plot.png", b"x" * 64, "file")])

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runs.shutil, "copyfileobj", refuse)

    with pytest.raises(runs.RunsError) as caught:
        extract(payload, destination)

    message = str(caught.value)
    assert "écriture impossible" in message, message
    assert "plot.png" in message, message
    assert "archive" not in message.lower(), "l'archive n'est pas en cause ici"
    assert not (destination / "plot.png").exists(), "aucun fichier partiel ne doit rester"
