"""Memory routes: RAG state, RAG reload, SiYuan blocks.

Three facts shaped this module, and it is built around them rather than around
what a client would like to hear:

* **there is no index builder in this repository.** ``index.faiss``,
  ``chunks.jsonl`` and ``manifeste.json`` are compiled from the operator's notes
  (``uhb/services/rag/README.md`` says so: "not shipped... they are the second
  brain, not the software"). A ``POST /rag/reindex`` that shelled out to an
  ``indexer.py`` would shell out to nothing, so this route does the one real
  action that exists: it asks the running RAG service to **reload** the index it
  already has (``POST /recharger`` on port 8200, see ``serveur_rag.py``). When
  that service is not up, the route says so instead of reporting a fake success;
* **the service is separate** (FastAPI on 8200). UHB owns the router, not the
  embeddings: it reports and it delegates, it does not import torch;
* **SiYuan answers on 6806** behind a token, and its SQL endpoint is the only way
  to list blocks by path. The token is read by name from the environment and sent
  as a header; it never appears in a payload, a log line or an exception.

Nothing here writes memory. ``rag_reload`` is the only mutating call, and it
mutates caches inside a service that is already running.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

#: The RAG service, started by hand (`python serveur_rag.py`), not by UHB.
RAG_SERVICE_URL = "http://127.0.0.1:8200"
#: Where the index files live, relative to the package (`uhb/services/rag`).
RAG_DIR = Path(__file__).resolve().parents[1] / "services" / "rag"
#: SiYuan's kernel, and the name of its token in the environment.
SIYUAN_URL = "http://127.0.0.1:6806"
SIYUAN_TOKEN_ENV = "SIYUAN_TOKEN"
#: Above this, counting the lines of chunks.jsonl costs more than it tells.
MAX_COUNT_BYTES = 20 * 1024 * 1024
#: A block listing is a peek, not an export.
MAX_BLOCKS = 500
DEFAULT_BLOCKS = 50


class MemoryRouteError(RuntimeError):
    """A memory route cannot answer. ``status`` is the HTTP code to use."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat(timespec="seconds")


def _count_lines(path: Path) -> int | None:
    try:
        if path.stat().st_size > MAX_COUNT_BYTES:
            return None
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# RAG
# --------------------------------------------------------------------------- #
def rag_status(rag_dir: Path | None = None, service_url: str = RAG_SERVICE_URL,
               timeout: float = 1.0, client: httpx.Client | None = None) -> dict:
    """What is on disk, and whether the service that reads it is up."""
    folder = Path(rag_dir) if rag_dir is not None else RAG_DIR
    index = folder / "index.faiss"
    chunks = folder / "chunks.jsonl"
    manifest = folder / "manifeste.json"
    built = index.is_file()
    documents: int | None = None
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
            if isinstance(payload, dict):
                for key in ("documents", "docs", "sources"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        documents = len(value)
                        break
        except (OSError, ValueError):
            documents = None
    service: dict = {}
    try:
        with (client or httpx.Client(timeout=timeout)) as http:
            response = http.get(f"{service_url.rstrip('/')}/sante")
        if response.status_code == 200:
            asked = response.json()
            service = asked if isinstance(asked, dict) else {"status": "ok"}
    except Exception:  # noqa: BLE001 - a probe that fails is a "no"
        service = {}
    # The service may hold the index somewhere else entirely (compiled from the
    # operator's notes, not shipped in this package): its fragment count is the
    # better truth, and the answer says which source it came from.
    fragments = service.get("fragments")
    if isinstance(fragments, int) and fragments > 0:
        built = True
    return {
        "built": built,
        "source": "service" if isinstance(fragments, int) else "disk",
        "last_indexed": _iso(index.stat().st_mtime) if index.is_file() else None,
        "documents": documents,
        "chunks": fragments if isinstance(fragments, int) else (
            _count_lines(chunks) if chunks.is_file() else None
        ),
        "index_bytes": index.stat().st_size if index.is_file() else 0,
        "service": bool(service),
        "service_url": service_url.rstrip("/"),
        "path": str(folder),
    }


def rag_reload(service_url: str = RAG_SERVICE_URL, timeout: float = 5.0,
               client: httpx.Client | None = None) -> dict:
    """Ask the RAG service to reload its index from disk.

    This is *reload*, not *reindex*: the index is compiled from the operator's
    notes by tooling that is not in this repository. The route name says what the
    caller wants; the payload says what actually happened.
    """
    url = f"{service_url.rstrip('/')}/recharger"
    try:
        with (client or httpx.Client(timeout=timeout)) as http:
            response = http.post(url)
    except Exception as exc:
        raise MemoryRouteError(
            f"the RAG service is not reachable at {service_url.rstrip('/')} "
            f"({type(exc).__name__}); start it with `python uhb/services/rag/serveur_rag.py`",
            status=409,
        ) from exc
    if response.status_code != 200:
        raise MemoryRouteError(
            f"the RAG service refused the reload (HTTP {response.status_code})", status=502
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return {
        "reloaded": True,
        "service_url": service_url.rstrip("/"),
        "service": payload if isinstance(payload, dict) else {},
    }


# --------------------------------------------------------------------------- #
# SiYuan
# --------------------------------------------------------------------------- #
def siyuan_blocks(
    *,
    limit: int = DEFAULT_BLOCKS,
    path: str = "",
    url: str | None = None,
    token: str | None = None,
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> dict:
    """List SiYuan blocks, optionally filtered by human path (`hpath`).

    The token is read from ``SIYUAN_TOKEN`` by name - the parameter exists so a
    test can pass a fake, and no payload ever echoes it back.
    """
    base = (url or os.environ.get("SIYUAN_URL") or SIYUAN_URL).rstrip("/")
    secret = token if token is not None else os.environ.get(SIYUAN_TOKEN_ENV, "")
    if not secret:
        raise MemoryRouteError(
            f"SiYuan is not configured: set {SIYUAN_TOKEN_ENV} (and SIYUAN_URL) "
            "before asking for blocks",
            status=400,
        )
    wanted = max(1, min(int(limit or DEFAULT_BLOCKS), MAX_BLOCKS))
    statement = "SELECT id, parent_id, type, content, hpath, updated FROM blocks"
    if path.strip():
        # Parameter binding is not offered by this endpoint: the value is escaped
        # for the single-quoted literal it lands in, and the length is capped.
        needle = path.strip().replace("'", "''")[:200]
        statement += f" WHERE hpath LIKE '%{needle}%'"
    statement += f" ORDER BY updated DESC LIMIT {wanted}"
    try:
        with (client or httpx.Client(timeout=timeout)) as http:
            response = http.post(
                f"{base}/api/query/sql",
                json={"stmt": statement},
                headers={"Authorization": f"Token {secret}"},
            )
    except Exception as exc:
        raise MemoryRouteError(
            f"SiYuan is not reachable at {base} ({type(exc).__name__})", status=502
        ) from exc
    if response.status_code == 401:
        raise MemoryRouteError("SiYuan refused the token (HTTP 401)", status=401)
    if response.status_code != 200:
        raise MemoryRouteError(f"SiYuan answered HTTP {response.status_code}", status=502)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MemoryRouteError("SiYuan answered something that is not JSON", status=502) from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("msg") or payload.get("message") or "")
        raise MemoryRouteError(f"SiYuan refused the query: {message or 'unknown error'}", status=502)
    data = payload.get("data")
    blocks = data if isinstance(data, list) else []
    return {
        "count": len(blocks),
        "blocks": [
            {
                "id": block.get("id"),
                "parent_id": block.get("parent_id"),
                "type": block.get("type"),
                "content": block.get("content"),
                "hpath": block.get("hpath"),
                "updated": block.get("updated"),
            }
            for block in blocks
            if isinstance(block, dict)
        ],
        "url": base,
        "path": path.strip() or None,
    }


__all__ = [
    "DEFAULT_BLOCKS",
    "MAX_BLOCKS",
    "RAG_DIR",
    "RAG_SERVICE_URL",
    "SIYUAN_TOKEN_ENV",
    "SIYUAN_URL",
    "MemoryRouteError",
    "rag_reload",
    "rag_status",
    "siyuan_blocks",
]
