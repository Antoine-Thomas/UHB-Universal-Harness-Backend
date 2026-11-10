"""``SecondBrainAdapter`` — read-only client for the local Hermes Home Vision services.

This is **not** a ``UHBAdapter``: it implements none of the eight contract functions
and knows nothing about harnesses. It reads three loopback services and nothing
else, because that is exactly what a control plane needs when it wants to ask
"what do my notes say about this?" before spending a model call.

Endpoints were measured on the running services, not guessed:

===========================  ==========================================  ===================================
Service                      Call                                        Answer
===========================  ==========================================  ===================================
RAG (8200)                   ``GET /sante`` · ``POST /search``           ``{"fragments", "modele", "par_source"}`` · ``{"resultats": [...]}``
SiYuan (6806)                ``POST /api/system/version``                ``{"code": 0, "data": "3.8.6"}`` (no token needed)
SiYuan content               ``POST /api/export/exportMdContent``        ``{"code": 0, "data": {"content", "hPath"}}`` (**token required**)
MARI bridge (55553)          ``GET /status``                             ``{"status": "ok", "signalsEmitted": N}``
===========================  ==========================================  ===================================

Fail-closed, at this layer:

- every call has a timeout; a hung service cannot hang the caller
- a content call that fails **raises** (:class:`SecondBrainError`) — it never
  returns an empty list that would read like "your second brain has nothing"
- ``status()`` never raises: a service being down is a *state*, not an accident
- the SiYuan token is read from ``SIYUAN_TOKEN`` by name, sent as a header, and
  never logged, stored, or returned

    from uhb.adapters.second_brain import SecondBrainAdapter

    brain = SecondBrainAdapter()
    brain.status()                       # [ServiceState(...), ...]
    brain.search("Comment fonctionne le Bridge MARI ?", k=3)
    brain.read_siyuan("20261109131511-donr63p")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

#: Where each service lives. Loopback only: these are local data sources.
RAG_URL = "http://127.0.0.1:8200"
SIYUAN_URL = "http://127.0.0.1:6806"
MARI_URL = "http://127.0.0.1:55553"

#: A status probe must be cheap: 2 s is already generous on loopback.
PROBE_TIMEOUT = 2.0

#: A search embeds the question (e5-base, ~1 s locally) — allow for a cold index.
SEARCH_TIMEOUT = 30.0

#: A page export can be large; still bounded.
READ_TIMEOUT = 20.0

#: The SiYuan API token, by NAME. Its value never leaves the environment.
SIYUAN_TOKEN_ENV = "SIYUAN_TOKEN"


class SecondBrainError(RuntimeError):
    """A read that could not be honoured. Never a silent empty answer."""


@dataclass(frozen=True, slots=True)
class ServiceState:
    """One probe. ``online`` is a fact, ``detail`` is what the service said."""

    name: str
    url: str
    online: bool
    detail: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "online": self.online,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 1),
        }


@dataclass(frozen=True, slots=True)
class Fragment:
    """One retrieved passage. ``path`` is display-shortened, never absolute."""

    source: str
    title: str
    part: int
    score: float
    excerpt: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "part": self.part,
            "score": round(self.score, 4),
            "excerpt": self.excerpt,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class SearchResult:
    question: str
    k: int
    fragments: list[Fragment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "k": self.k,
            "fragments": [fragment.to_dict() for fragment in self.fragments],
        }


def shorten_path(raw: str) -> str:
    """``<home>/notes/a.md`` -> ``~/notes/a.md``. Display only, never a write."""
    if not raw:
        return ""
    try:
        return "~/" + Path(raw).resolve().relative_to(Path.home().resolve()).as_posix()
    except (ValueError, OSError):
        return Path(raw).name


class SecondBrainAdapter:
    """Three read-only calls, one token, bounded timeouts."""

    def __init__(
        self,
        *,
        rag_url: str = RAG_URL,
        siyuan_url: str = SIYUAN_URL,
        mari_url: str = MARI_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = PROBE_TIMEOUT,
        search_timeout: float = SEARCH_TIMEOUT,
        token_env: str = SIYUAN_TOKEN_ENV,
    ) -> None:
        self.rag_url = rag_url.rstrip("/")
        self.siyuan_url = siyuan_url.rstrip("/")
        self.mari_url = mari_url.rstrip("/")
        self.timeout = timeout
        self.search_timeout = search_timeout
        self.token_env = token_env
        self._transport = transport

    # -- plumbing -----------------------------------------------------------

    def _client(self, timeout: float) -> httpx.Client:
        # The transport is injectable so tests never open a socket.
        return httpx.Client(timeout=timeout, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        token = os.environ.get(self.token_env)
        if token:
            # Sent, never stored: the value is not kept on the instance.
            headers["authorization"] = f"Token {token}"
        return headers

    @staticmethod
    def _require(ok: bool, message: str) -> None:
        if not ok:
            raise SecondBrainError(message)

    # -- status -------------------------------------------------------------

    def status(self) -> list[ServiceState]:
        """Probe the three services. A failure is a state, never an exception."""
        return [
            self._probe("rag", self.rag_url, self._probe_rag),
            self._probe("siyuan", self.siyuan_url, self._probe_siyuan),
            self._probe("mari", self.mari_url, self._probe_mari),
        ]

    def _probe(self, name: str, url: str, call) -> ServiceState:
        import time

        started = time.perf_counter()
        try:
            detail = call()
            online = True
        except Exception as exc:  # a down service is a state, not a crash
            detail, online = type(exc).__name__, False
        return ServiceState(
            name=name,
            url=url,
            online=online,
            detail=detail,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _probe_rag(self) -> str:
        with self._client(self.timeout) as client:
            response = client.get(f"{self.rag_url}/sante")
        self._require(response.status_code == 200, f"rag: HTTP {response.status_code}")
        data = response.json()
        return f"{data.get('fragments', 0)} fragments · {data.get('modele', '?')}"

    def _probe_siyuan(self) -> str:
        with self._client(self.timeout) as client:
            response = client.post(f"{self.siyuan_url}/api/system/version", json={})
        self._require(response.status_code == 200, f"siyuan: HTTP {response.status_code}")
        body = response.json()
        return f"v{body.get('data', '?')}"

    def _probe_mari(self) -> str:
        with self._client(self.timeout) as client:
            response = client.get(f"{self.mari_url}/status")
        self._require(response.status_code == 200, f"mari: HTTP {response.status_code}")
        data = response.json()
        return f"status={data.get('status', '?')} · signals={data.get('signalsEmitted', '?')}"

    # -- search -------------------------------------------------------------

    def search(self, question: str, k: int = 5) -> SearchResult:
        """Ask the RAG. Raises rather than returning an empty list on failure."""
        question = (question or "").strip()
        self._require(bool(question), "search needs a non-empty question")
        self._require(1 <= k <= 20, f"k must be between 1 and 20, got {k}")
        try:
            with self._client(self.search_timeout) as client:
                response = client.post(
                    f"{self.rag_url}/search", json={"question": question, "k": k}
                )
        except httpx.TimeoutException as exc:
            raise SecondBrainError(
                f"rag: no answer within {self.search_timeout:.0f}s — the service is up but slow"
            ) from exc
        self._require(response.status_code == 200, f"rag: HTTP {response.status_code}")
        data = response.json()
        fragments = [
            Fragment(
                source=str(item.get("source") or ""),
                title=str(item.get("titre") or ""),
                part=int(item.get("partie") or 0),
                score=float(item.get("score") or 0.0),
                excerpt=str(item.get("extrait") or "").strip(),
                path=shorten_path(str(item.get("chemin") or "")),
            )
            for item in (data.get("resultats") or [])
        ]
        return SearchResult(question=question, k=k, fragments=fragments)

    # -- SiYuan -------------------------------------------------------------

    def read_siyuan(self, page_id: str) -> dict[str, str]:
        """Export one SiYuan document as markdown: ``{"id", "hPath", "markdown"}``."""
        page_id = (page_id or "").strip()
        self._require(bool(page_id), "read_siyuan needs a document id")
        self._require(
            bool(os.environ.get(self.token_env)),
            f"{self.token_env} is not set: SiYuan refuses content reads without it "
            "(its /api/system/version answers without auth, content does not)",
        )
        try:
            with self._client(READ_TIMEOUT) as client:
                response = client.post(
                    f"{self.siyuan_url}/api/export/exportMdContent",
                    json={"id": page_id},
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise SecondBrainError(f"siyuan: no answer within {READ_TIMEOUT:.0f}s") from exc
        self._require(response.status_code == 200, f"siyuan: HTTP {response.status_code}")
        body = response.json()
        self._require(
            int(body.get("code", -1)) == 0,
            f"siyuan: {body.get('msg') or 'unknown error'} (code {body.get('code')})",
        )
        data = body.get("data") or {}
        return {
            "id": page_id,
            "hPath": str(data.get("hPath") or ""),
            "markdown": str(data.get("content") or ""),
        }

    # -- MARI ---------------------------------------------------------------

    def mari_status(self) -> dict[str, Any]:
        """The bridge's only read-only endpoint."""
        try:
            with self._client(self.timeout) as client:
                response = client.get(f"{self.mari_url}/status")
        except httpx.TimeoutException as exc:
            raise SecondBrainError(f"mari: no answer within {self.timeout:.0f}s") from exc
        self._require(response.status_code == 200, f"mari: HTTP {response.status_code}")
        return response.json()
