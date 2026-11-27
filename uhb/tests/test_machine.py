"""G19 — la machine est mesurée, le catalogue est une politique, l'accord est un clic.

Ce que ces tests prouvent, et rien de plus :

* le profil ne devine rien (``unknown`` nomme ce qui manque) ;
* ``recommend`` est une fonction pure : quatre profils, quatre réponses différentes,
  et jamais une entrée qui ne tient pas ;
* le MoE déchargé en RAM est proposé **avec** son avertissement et **jamais**
  coché d'avance ;
* aucun téléchargement ne part sans ``accept: true`` (compteur d'appels à zéro) ;
* les routes ``/machine/*`` et ``/models/*`` passent par le vrai serveur.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from uhb.core import machine


def _wait_done(run_id: str, timeout: float = 10.0) -> dict:
    """Attend l'état final ; sous charge, un fil peut mettre plus de 2 s."""
    deadline = time.monotonic() + timeout
    state = machine.pull_status(run_id)
    while state["state"] not in {"done", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        state = machine.pull_status(run_id)
    return state


def profile(
    vram: float | None = 8.0,
    ram_free: float | None = 46.0,
    ram_total: float | None = 64.0,
) -> dict:
    gpu = (
        None
        if vram is None
        else {
            "vendor": "nvidia",
            "name": "RTX 3070 Ti",
            "vram_total_gb": 8.0,
            "vram_free_gb": vram,
            "source": "test",
        }
    )
    return {
        "os": "Windows",
        "arch": "AMD64",
        "python": "3.10.0",
        "cpu": {"model": "test", "cores": 16},
        "ram": {"total_gb": ram_total, "free_gb": ram_free, "source": "test"},
        "disk": {"home_free_gb": 800.0},
        "gpu": gpu,
        "unknown": [] if gpu else ["gpu"],
    }


CATALOG = machine.load_catalog()


# --- le catalogue ---------------------------------------------------------- #


def test_the_catalog_refuses_an_incomplete_entry(tmp_path: Path) -> None:
    broken = tmp_path / "catalog.json"
    broken.write_text(
        json.dumps({"version": "0", "entries": [{"id": "x", "engine": "ollama"}]}),
        encoding="utf-8",
    )

    with pytest.raises(machine.MachineError, match="entrée incomplète"):
        machine.load_catalog(broken)


def test_inkling_is_present_but_not_presented_as_safe() -> None:
    entry = next(e for e in CATALOG["entries"] if e["id"].startswith("inkling-2-mini"))

    assert entry["file_gb"] == 18.0
    assert entry["needs"]["vram_gb"] == 19.3
    assert entry["moe"] is True and entry["offloadable"] is True
    assert entry["verified"] is False, "aucun hash publié, aucune mesure locale"
    assert entry["license"] == "Apache-2.0"
    assert "ne tient pas en 8 Go" in entry["notes"]


# --- recommandation -------------------------------------------------------- #


def test_eight_gigabytes_gets_the_biggest_that_really_fits() -> None:
    verdict = machine.recommend(profile(8.0, 46.0), CATALOG)

    assert verdict["entry"]["id"] == "qwen2.5:7b"
    assert verdict["entry"]["mode"] == "vram"
    assert verdict["entry"]["default_checked"] is True
    inkling = next(a for a in verdict["alternatives"] if a["id"].startswith("inkling"))
    assert inkling["mode"] == "offload"
    assert inkling["default_checked"] is False
    assert "ne tient pas en 8 Go de VRAM" in inkling["warning"]


def test_four_gigabytes_descends_instead_of_pretending() -> None:
    verdict = machine.recommend(profile(4.0, 46.0), CATALOG)

    assert verdict["entry"]["id"] == "lfm3"
    assert verdict["entry"]["needs"]["vram_gb"] <= 4.0


def test_twenty_four_gigabytes_reaches_the_mini() -> None:
    verdict = machine.recommend(profile(24.0, 200.0), CATALOG)

    assert verdict["entry"]["id"].startswith("inkling-2-mini")
    assert verdict["entry"]["mode"] == "vram"
    assert verdict["entry"]["verified"] is False


def test_a_small_machine_cannot_offload_the_mini() -> None:
    """8 Go de VRAM + 8 Go de RAM ne couvrent pas 19,3 + 0,8 : refus, pas espoir."""
    verdict = machine.recommend(profile(8.0, 8.0, 16.0), CATALOG)

    assert verdict["entry"]["id"] == "qwen2.5:7b"
    assert verdict["alternatives"] == []


def test_a_dense_model_is_never_offloaded() -> None:
    """Le déchargement RAM ne se justifie que pour des experts : pas pour qwen3.5."""
    verdict = machine.recommend(profile(4.0, 400.0), CATALOG)

    assert verdict["entry"]["id"] == "lfm3"
    assert all(a["id"] != "qwen3.5:9b" for a in verdict["alternatives"])


def test_an_unmeasured_gpu_gets_the_prudent_tier() -> None:
    verdict = machine.recommend(profile(None), CATALOG)

    assert verdict["entry"]["mode"] == "cpu"
    assert verdict["entry"]["needs"]["vram_gb"] <= machine.CPU_ONLY_MAX_GB
    assert any("VRAM non mesurée" in reason for reason in verdict["reasons"])


def test_recommend_is_pure() -> None:
    measured = profile(8.0, 46.0)

    first = machine.recommend(measured, CATALOG)
    second = machine.recommend(measured, CATALOG)

    assert first == second


# --- profil mesuré --------------------------------------------------------- #


def test_a_missing_probe_becomes_unknown_not_a_number(monkeypatch) -> None:
    monkeypatch.setattr(machine, "measure_gpu", lambda: None)
    monkeypatch.setattr(
        machine, "_ram", lambda: {"total_gb": None, "free_gb": None, "source": None}
    )

    measured = machine.measure_profile()

    assert measured["gpu"] is None
    assert "gpu" in measured["unknown"]
    assert "ram_free_gb" in measured["unknown"]


def test_the_real_profile_measures_something_here() -> None:
    measured = machine.measure_profile()

    assert measured["os"] in {"Windows", "Linux", "Darwin"}
    assert measured["cpu"]["cores"] and measured["cpu"]["cores"] > 0
    # La VRAM est lue par nvidia-smi sur ce poste ; ailleurs elle reste inconnue,
    # et c'est `unknown` qui le dit.
    assert measured["gpu"] is None or measured["gpu"]["source"]


# --- la porte d'accord ----------------------------------------------------- #


def test_no_accept_means_no_download(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UHB_MODELS_STATUS_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        machine, "_ollama_pull", lambda tag, progress=None: calls.append(tag) or {}
    )

    with pytest.raises(machine.MachineError) as refusal:
        machine.start_pull(
            "qwen2.5:7b", accept=False, profile=profile(8.0), catalog=CATALOG
        )

    assert refusal.value.status == 403
    assert "accord explicite" in str(refusal.value)
    assert calls == [], "aucun appel à Ollama sans accord"
    assert list(tmp_path.glob("pull-*.json")) == [], "aucun fichier de suivi non plus"


def test_accepting_starts_one_pull_and_records_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UHB_MODELS_STATUS_DIR", str(tmp_path))
    seen: list[str] = []

    def fake_pull(tag: str, progress=None):
        seen.append(tag)
        if progress:
            progress({"status": "pulling manifest"})
        return {"status": "success"}

    monkeypatch.setattr(machine, "_ollama_pull", fake_pull)

    started = machine.start_pull(
        "qwen2.5:7b", accept=True, profile=profile(8.0), catalog=CATALOG
    )
    assert started["started"] is True
    assert started["mode"] == "vram"

    final = _wait_done(started["id"])

    assert seen == ["qwen2.5:7b"]
    assert final["state"] == "done", final


def test_a_model_that_does_not_fit_is_refused_even_with_accept() -> None:
    entry = next(e for e in CATALOG["entries"] if e["id"].startswith("inkling-2-mini"))

    with pytest.raises(machine.MachineError) as refusal:
        machine.authorise_pull(entry, profile(8.0, 8.0, 16.0), accept=True)

    assert refusal.value.status == 409
    assert "19.3 Go" in str(refusal.value)


def test_the_offload_mode_is_named_when_it_is_the_one_used() -> None:
    entry = next(e for e in CATALOG["entries"] if e["id"].startswith("inkling-2-mini"))

    assert (
        machine.authorise_pull(entry, profile(8.0, 46.0, 64.0), accept=True)
        == "offload"
    )


# --- les routes réelles ---------------------------------------------------- #


@pytest.fixture()
def server(monkeypatch, tmp_path: Path):
    from uhb.entrypoints import server as server_module

    monkeypatch.setenv("UHB_MODELS_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(machine, "measure_profile", lambda: profile(8.0, 46.0))
    httpd = server_module.make_server(0, "127.0.0.1")
    threading.Thread(
        target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True
    ).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(port: int, method: str, path: str, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read().decode() or "null")
    conn.close()
    return response.status, data


def test_the_catalog_is_readable_through_the_api(server) -> None:
    status, data = _call(server, "GET", "/models/catalog")

    assert status == 200
    assert data["version"] == "1"


def test_the_profile_route_answers_with_the_measured_shape(server) -> None:
    status, data = _call(server, "GET", "/machine/profile")

    assert status == 200
    assert data["gpu"]["vram_free_gb"] == 8.0
    assert "unknown" in data


def test_the_scan_route_recommends_and_counts_nothing(server, tmp_path: Path) -> None:
    status, data = _call(server, "POST", "/machine/scan", {})

    assert status == 200
    assert data["entry"]["id"] == "qwen2.5:7b"
    assert list(tmp_path.glob("pull-*.json")) == [], "un scan ne télécharge rien"


def test_the_pull_route_refuses_without_accept(server) -> None:
    status, data = _call(server, "POST", "/models/pull", {"entry_id": "qwen2.5:7b"})

    assert status == 403
    assert "accord explicite" in data["error"]["message"]


def test_the_pull_route_starts_with_accept(server, monkeypatch, tmp_path: Path) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        machine,
        "_ollama_pull",
        lambda tag, progress=None: seen.append(tag) or {"status": "success"},
    )

    status, data = _call(
        server, "POST", "/models/pull", {"entry_id": "qwen2.5:7b", "accept": True}
    )

    assert status == 202
    assert data["started"] is True
    assert _wait_done(data["id"])["state"] == "done"
    assert seen == ["qwen2.5:7b"]
    follow_status, follow = _call(server, "GET", data["status_url"])
    assert follow_status == 200 and follow["state"] == "done"


def test_the_catalog_lives_inside_the_package_so_it_ships() -> None:
    """Une politique absente du wheel, c'est un produit qui démarre sans catalogue."""
    assert machine.CATALOG_PATH.is_file()
    assert machine.CATALOG_PATH.parent == Path(machine.__file__).parent
