"""The RAG service must start and answer honestly without an index.

An index is not shipped with the package (see ``uhb/services/rag/README.md``): a
fresh clone — and therefore the ``rag`` container of ``docker-compose.yml`` —
has no ``manifeste.json``. The service used to raise at startup in that state,
so ``/sante`` was unreachable and the container crash-looped, contradicting the
README's "a fresh clone starts with an empty brain, ``/sante`` answers
``fragments: 0``".

Two behaviours are pinned here, and they pull in opposite directions on purpose:

* ``/sante`` answers 200 with ``fragments: 0`` — empty, not broken, so UHB can
  report the state of the second brain;
* ``/search`` and ``/recharger`` answer 503 — never an empty result set, which
  would read as "your brain contains nothing".

The service is loaded by path: it is a script next to its engine
(``chercher.py``), not an installed package. ``fastapi`` is an optional
dependency of the RAG service, so the module is skipped where it is absent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the RAG service is an optional extra")
pytest.importorskip("httpx", reason="TestClient speaks HTTP")

from fastapi.testclient import TestClient  # noqa: E402

SERVICE_DIR = Path(__file__).resolve().parents[1] / "services" / "rag"
SERVICE_FILE = SERVICE_DIR / "serveur_rag.py"


def load_service():
    """Import ``serveur_rag.py`` the way it runs: with its own directory on sys.path."""
    directory = str(SERVICE_DIR)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    if "uhb_rag_service_under_test" in sys.modules:
        return sys.modules["uhb_rag_service_under_test"]
    spec = importlib.util.spec_from_file_location("uhb_rag_service_under_test", SERVICE_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["uhb_rag_service_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The service pointed at an empty directory: no index, no manifest."""
    module = load_service()
    monkeypatch.setattr(module, "MANIFESTE", str(tmp_path / "manifeste.json"))
    monkeypatch.setattr(module, "INDEX", str(tmp_path / "index.faiss"))
    monkeypatch.setattr(module, "LOG_RECHERCHES", str(tmp_path / "recherches.log"))
    return module


def test_the_service_imports_and_builds_an_app(service) -> None:
    assert service.app is not None


def test_an_empty_brain_answers_zero_and_says_why(service) -> None:
    response = TestClient(service.app).get("/sante")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "sans_index"
    assert payload["fragments"] == 0
    assert payload["modele"] is None
    assert payload["index_mo"] is None
    assert "README" in payload["detail"]


def test_a_search_without_an_index_is_a_503_not_an_empty_list(service) -> None:
    response = TestClient(service.app).post("/search", json={"question": "où est le routeur ?"})

    assert response.status_code == 503
    assert "manifeste" in response.json()["detail"]


def test_a_reload_without_an_index_is_a_503(service) -> None:
    assert TestClient(service.app).post("/recharger").status_code == 503


def test_embeddings_without_an_index_is_a_503(service) -> None:
    response = TestClient(service.app).post("/v1/embeddings", json={"input": "texte"})

    assert response.status_code == 503


def test_a_manifest_is_reported_as_it_is(
    service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifeste.json"
    manifest.write_text(
        json.dumps({"modele": "intfloat/multilingual-e5-base", "fragments": 12,
                    "par_source": {"wiki": 12}}),
        encoding="utf-8",
    )

    payload = TestClient(service.app).get("/sante").json()

    assert payload["status"] == "ok"
    assert payload["fragments"] == 12
    assert payload["modele"] == "intfloat/multilingual-e5-base"
    assert payload["par_source"] == {"wiki": 12}
