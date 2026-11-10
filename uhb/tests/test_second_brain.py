"""SecondBrainAdapter (Phase 3): RAG, SiYuan and MARI, mocked at the transport.

No socket is opened here: every call goes through an ``httpx.MockTransport`` whose
handler asserts the request shape the real services expect. The real endpoints
were measured once, by hand, on the running services — these tests freeze them.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uhb.adapters.second_brain import (
    Fragment,
    SearchResult,
    SecondBrainAdapter,
    SecondBrainError,
    ServiceState,
    SIYUAN_TOKEN_ENV,
    shorten_path,
)
from uhb.entrypoints import cli

SANTE = {
    "status": "ok",
    "fragments": 4434,
    "modele": "intfloat/multilingual-e5-base",
    "par_source": {"siyuan": 496, "skill": 3494, "wiki": 355, "wordpress": 89},
    "index_mo": 13.6,
}

SEARCH = {
    "question": "Comment fonctionne le Bridge MARI ?",
    "k": 2,
    "source": None,
    "resultats": [
        {
            "score": 0.0331,
            "cos": 0.855,
            "source": "skill",
            "titre": "software-development/mari-orleans-integration/SKILL.md",
            "partie": 16,
            "chemin": str(Path.home() / "skills" / "mari" / "SKILL.md"),
            "extrait": "Le binaire vit dans bin/Debug/net8.0/Bridge.exe.\nLa forme qui marche.",
        },
        {
            "score": 0.0328,
            "cos": 0.851,
            "source": "wiki",
            "titre": "concepts/mari-reactor.md",
            "partie": 2,
            "chemin": str(Path.home() / "notes" / "mari-reactor.md"),
            "extrait": "Un watcher Python lit l'etat de sante ANIMA toutes les 30 s.",
        },
    ],
}

MARI = {"status": "ok", "signalsEmitted": 2}


class Roster:
    """A handler per route, with per-route failures. Records every request."""

    def __init__(self, **failures: str) -> None:
        self.failures = failures  # path fragment -> "timeout" | "500"
        self.seen: list[tuple[str, str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.seen.append(
            (request.method, request.url.path, request.url.host, {"body": body,
             "auth": request.headers.get("authorization", "")})
        )
        path = request.url.path
        for fragment, mode in self.failures.items():
            if fragment in path:
                if mode == "timeout":
                    raise httpx.ReadTimeout("too slow", request=request)
                return httpx.Response(500, json={"detail": "boom"})
        if path == "/sante":
            return httpx.Response(200, json=SANTE)
        if path == "/search":
            return httpx.Response(200, json=SEARCH)
        if path == "/api/system/version":
            return httpx.Response(200, json={"code": 0, "data": "3.8.6"})
        if path == "/api/export/exportMdContent":
            return httpx.Response(200, json={"code": 0, "data": {"hPath": "/2026-11-09-veille",
                                                                "content": "# veille\n\nligne"}})
        if path == "/status":
            return httpx.Response(200, json=MARI)
        return httpx.Response(404, json={"error": "unknown"})

    def paths(self) -> list[str]:
        return [entry[1] for entry in self.seen]


def brain(handler: Roster) -> SecondBrainAdapter:
    return SecondBrainAdapter(transport=httpx.MockTransport(handler), timeout=0.5, search_timeout=1.0)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    monkeypatch.delenv(SIYUAN_TOKEN_ENV, raising=False)


# --- status ----------------------------------------------------------------


def test_status_probes_the_three_real_endpoints():
    roster = Roster()
    states = brain(roster).status()

    assert [state.name for state in states] == ["rag", "siyuan", "mari"]
    assert all(state.online for state in states)
    assert states[0].detail == "4434 fragments · intfloat/multilingual-e5-base"
    assert states[1].detail == "v3.8.6"
    assert states[2].detail == "status=ok · signals=2"
    assert roster.paths() == ["/sante", "/api/system/version", "/status"]


def test_status_of_a_down_service_is_a_state_not_a_crash():
    roster = Roster(**{"/status": "timeout"})
    states = brain(roster).status()

    assert [state.online for state in states] == [True, True, False]
    assert states[2].detail == "ReadTimeout", "the class names the failure, never a payload"
    assert "timeout" not in states[2].detail


def test_status_survives_a_500_on_one_service():
    states = brain(Roster(**{"/api/system/version": "500"})).status()

    assert [state.online for state in states] == [True, False, True]
    assert states[1].detail == "SecondBrainError"


# --- search ----------------------------------------------------------------


def test_search_sends_the_question_and_k_to_the_rag():
    roster = Roster()
    result = brain(roster).search("Comment fonctionne le Bridge MARI ?", k=2)

    method, path, host, extra = roster.seen[0]
    assert (method, path, host) == ("POST", "/search", "127.0.0.1")
    assert extra["body"] == {"question": "Comment fonctionne le Bridge MARI ?", "k": 2}
    assert len(result.fragments) == 2
    assert result.fragments[0].source == "skill"
    assert result.fragments[0].part == 16
    assert result.fragments[0].score == pytest.approx(0.0331)


def test_search_excerpts_keep_their_newlines_for_the_caller_to_fold():
    fragment = brain(Roster()).search("q", k=2).fragments[0]

    assert "\n" in fragment.excerpt, "the adapter does not reformat the passage"


def test_a_failed_search_raises_instead_of_returning_nothing():
    with pytest.raises(SecondBrainError) as caught:
        brain(Roster(**{"/search": "500"})).search("q")

    assert "rag: HTTP 500" in str(caught.value)


def test_a_slow_search_names_the_timeout():
    with pytest.raises(SecondBrainError) as caught:
        brain(Roster(**{"/search": "timeout"})).search("q")

    assert "no answer within" in str(caught.value)


def test_an_empty_question_or_a_bad_k_is_refused_before_the_call():
    adapter = brain(Roster())

    with pytest.raises(SecondBrainError):
        adapter.search("   ")
    with pytest.raises(SecondBrainError):
        adapter.search("q", k=0)


# --- SiYuan ----------------------------------------------------------------


def test_read_siyuan_requires_the_token_and_says_why():
    with pytest.raises(SecondBrainError) as caught:
        brain(Roster()).read_siyuan("20261109131511-donr63p")

    assert SIYUAN_TOKEN_ENV in str(caught.value)
    assert "content does not" in str(caught.value)


def test_read_siyuan_sends_the_token_header_and_returns_markdown(monkeypatch):
    monkeypatch.setenv(SIYUAN_TOKEN_ENV, "fake-token-for-tests")
    roster = Roster()

    page = brain(roster).read_siyuan("20261109131511-donr63p")

    method, path, _host, extra = roster.seen[0]
    assert (method, path) == ("POST", "/api/export/exportMdContent")
    assert extra["body"] == {"id": "20261109131511-donr63p"}
    assert extra["auth"] == "Token fake-token-for-tests"
    assert page["hPath"] == "/2026-11-09-veille"
    assert page["markdown"].startswith("# veille")


def test_read_siyuan_surfaces_the_siyuan_error_code(monkeypatch):
    monkeypatch.setenv(SIYUAN_TOKEN_ENV, "fake-token-for-tests")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -1, "msg": "Auth failed [session]"})

    with pytest.raises(SecondBrainError) as caught:
        SecondBrainAdapter(transport=httpx.MockTransport(handler)).read_siyuan("x")

    assert "Auth failed" in str(caught.value)


# --- MARI and paths --------------------------------------------------------


def test_mari_status_returns_the_bridge_payload():
    assert brain(Roster()).mari_status() == MARI


def test_home_paths_are_shortened_for_display():
    inside = Path.home() / "notes" / "a.md"
    assert shorten_path(str(inside)) == "~/notes/a.md"
    assert shorten_path("/etc/hosts") == "hosts"


# --- CLI -------------------------------------------------------------------


def test_cli_brain_status_prints_one_line_per_service(monkeypatch, capsys):
    states = [
        ServiceState("rag", "http://127.0.0.1:8200", True, "4434 fragments", 76.0),
        ServiceState("siyuan", "http://127.0.0.1:6806", True, "v3.8.6", 31.0),
        ServiceState("mari", "http://127.0.0.1:55553", False, "ReadTimeout", 2002.0),
    ]
    monkeypatch.setattr(
        "uhb.adapters.second_brain.SecondBrainAdapter.status", lambda self: states
    )

    code = cli.main(["brain", "status"])
    out = capsys.readouterr().out

    assert code == 0, "a down service is a state, not a CLI failure"
    assert "● rag" in out and "● siyuan" in out and "○ mari" in out
    assert "down: mari" in out


def test_cli_brain_status_strict_exits_one_when_a_service_is_down(monkeypatch, capsys):
    monkeypatch.setattr(
        "uhb.adapters.second_brain.SecondBrainAdapter.status",
        lambda self: [ServiceState("mari", "http://127.0.0.1:55553", False, "ReadTimeout")],
    )

    assert cli.main(["brain", "status", "--strict"]) == 1
    assert "down: mari" in capsys.readouterr().out


def test_cli_brain_search_prints_the_passages(monkeypatch, capsys):
    monkeypatch.setattr(
        "uhb.adapters.second_brain.SecondBrainAdapter.search",
        lambda self, question, k=5: SearchResult(
            question=question,
            k=k,
            fragments=[
                Fragment(
                    source="skill", title="mari/SKILL.md", part=16, score=0.0331,
                    excerpt="Le binaire vit dans bin/Debug.\nLa forme qui marche.", path="~/skills/mari/SKILL.md",
                )
            ],
        ),
    )

    code = cli.main(["brain", "search", "Bridge MARI", "-k", "1"])
    out = capsys.readouterr().out

    assert code == 0
    assert "1 passage(s) pour « Bridge MARI »" in out
    assert "skill · mari/SKILL.md §16" in out
    assert "La forme qui marche." in out, "the excerpt is folded onto one line for the terminal"


def test_cli_brain_search_json_is_valid(monkeypatch, capsys):
    monkeypatch.setattr(
        "uhb.adapters.second_brain.SecondBrainAdapter.search",
        lambda self, question, k=5: SearchResult(question=question, k=k, fragments=[]),
    )

    code = cli.main(["brain", "search", "rien", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload == {"question": "rien", "k": 5, "fragments": []}


def test_cli_brain_read_prints_the_markdown(monkeypatch, capsys):
    monkeypatch.setattr(
        "uhb.adapters.second_brain.SecondBrainAdapter.read_siyuan",
        lambda self, page_id: {"id": page_id, "hPath": "/2026-11-09-veille",
                               "markdown": "# veille" + chr(10) * 2 + "ligne"},
    )

    code = cli.main(["brain", "read", "20261109131511-donr63p"])
    out = capsys.readouterr().out

    assert code == 0
    assert "/2026-11-09-veille" in out
    assert "# veille" in out
