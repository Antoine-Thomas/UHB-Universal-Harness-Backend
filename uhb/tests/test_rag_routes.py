"""RAG routes: what is on disk, and the one real action (a reload).

The route is called ``/rag/reindex`` because that is what a client wants; there
is no index builder in this repository, so these tests pin the honest behaviour:
report the disk, reload a running service, and refuse with a clear reason when
the service is down.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uhb.entrypoints import memory_routes


def fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def test_an_empty_brain_says_not_built(tmp_path: Path) -> None:
    state = memory_routes.rag_status(rag_dir=tmp_path, client=fake_client(lambda r: httpx.Response(503)))

    assert state["built"] is False
    assert state["last_indexed"] is None
    assert state["chunks"] is None
    assert state["service"] is False


def test_a_built_index_reports_its_shape(tmp_path: Path) -> None:
    (tmp_path / "index.faiss").write_bytes(b"0" * 4096)
    (tmp_path / "chunks.jsonl").write_text("{}\n{}\n{}\n", encoding="utf-8")
    (tmp_path / "manifeste.json").write_text(json.dumps({"documents": ["a", "b"]}), encoding="utf-8")

    state = memory_routes.rag_status(
        rag_dir=tmp_path, client=fake_client(lambda r: httpx.Response(200, json={"status": "ok"}))
    )

    assert state["built"] is True
    assert state["chunks"] == 3
    assert state["documents"] == 2
    assert state["index_bytes"] == 4096
    assert state["last_indexed"]
    assert state["service"] is True


def test_the_service_count_wins_over_an_absent_local_index(tmp_path: Path) -> None:
    """The operator's index lives in their notes, not in this package."""
    state = memory_routes.rag_status(
        rag_dir=tmp_path,
        client=fake_client(lambda r: httpx.Response(200, json={"status": "ok", "fragments": 4893})),
    )

    assert state["built"] is True
    assert state["source"] == "service"
    assert state["chunks"] == 4893
    assert state["index_bytes"] == 0, "no local file, and the payload does not pretend otherwise"


def test_the_service_probe_is_a_health_read(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    memory_routes.rag_status(rag_dir=tmp_path, client=fake_client(handler))

    assert seen == ["http://127.0.0.1:8200/sante"]


def test_a_broken_manifest_does_not_break_the_status(tmp_path: Path) -> None:
    (tmp_path / "index.faiss").write_bytes(b"0")
    (tmp_path / "manifeste.json").write_text("{not json", encoding="utf-8")

    state = memory_routes.rag_status(rag_dir=tmp_path, client=fake_client(lambda r: httpx.Response(503)))

    assert state["built"] is True
    assert state["documents"] is None


# --------------------------------------------------------------------------- #
# reload
# --------------------------------------------------------------------------- #
def test_a_reload_calls_recharger_and_reports_it() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json={"fragments": 42})

    result = memory_routes.rag_reload(client=fake_client(handler))

    assert seen == [("POST", "http://127.0.0.1:8200/recharger")]
    assert result["reloaded"] is True
    assert result["service"] == {"fragments": 42}


def test_a_reload_without_a_service_is_a_409_not_a_lie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.rag_reload(client=fake_client(handler))

    assert caught.value.status == 409
    assert "serveur_rag.py" in str(caught.value), "the message must say what to start"


def test_a_service_that_refuses_is_a_502() -> None:
    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.rag_reload(client=fake_client(lambda r: httpx.Response(500)))

    assert caught.value.status == 502


def test_a_reload_never_claims_to_have_rebuilt() -> None:
    result = memory_routes.rag_reload(client=fake_client(lambda r: httpx.Response(200, json={})))

    assert "reindexed" not in result
    assert result["reloaded"] is True
