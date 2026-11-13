"""SiYuan blocks: the SQL query, the token by name, and the refusals.

No SiYuan instance is required: the transport is mocked, which is the point -
these tests describe the contract UH depends on, not the user's kernel.
"""

from __future__ import annotations

import json

import httpx
import pytest

from uhb.entrypoints import memory_routes


def fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_blocks_are_listed_with_their_path() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("Authorization")
        seen["stmt"] = json.loads(request.content)["stmt"]
        return httpx.Response(200, json={"code": 0, "data": [
            {"id": "2026-a", "parent_id": "2026-p", "type": "p", "content": "le ciel est bleu",
             "hpath": "/concepts/meteo", "updated": "20261113120000"},
        ]})

    result = memory_routes.siyuan_blocks(limit=10, token="secret", client=fake_client(handler))

    assert seen["url"].endswith("/api/query/sql")
    assert seen["token"] == "Token secret"
    assert "LIMIT 10" in seen["stmt"]
    assert result["count"] == 1
    assert result["blocks"][0]["hpath"] == "/concepts/meteo"
    assert result["blocks"][0]["content"] == "le ciel est bleu"


def test_the_token_never_comes_back_in_the_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": []})

    result = memory_routes.siyuan_blocks(token="secret-token", client=fake_client(handler))

    assert "secret-token" not in json.dumps(result)


def test_a_path_filter_is_escaped_and_capped() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["stmt"] = json.loads(request.content)["stmt"]
        return httpx.Response(200, json={"code": 0, "data": []})

    memory_routes.siyuan_blocks(path="l'ami " + "x" * 500, token="t", client=fake_client(handler))

    assert "l''ami" in seen["stmt"], "a quote in the filter must not close the literal"
    assert seen["stmt"].count("x") <= 200


def test_the_limit_is_clamped() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["stmt"] = json.loads(request.content)["stmt"]
        return httpx.Response(200, json={"code": 0, "data": []})

    memory_routes.siyuan_blocks(limit=99999, token="t", client=fake_client(handler))

    assert f"LIMIT {memory_routes.MAX_BLOCKS}" in seen["stmt"]


def test_without_a_token_the_route_refuses_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(memory_routes.SIYUAN_TOKEN_ENV, raising=False)

    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.siyuan_blocks(client=fake_client(lambda r: httpx.Response(200)))

    assert caught.value.status == 400
    assert memory_routes.SIYUAN_TOKEN_ENV in str(caught.value)


def test_a_refused_token_is_a_401() -> None:
    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.siyuan_blocks(
            token="bad", client=fake_client(lambda r: httpx.Response(401))
        )

    assert caught.value.status == 401


def test_a_siyuan_error_code_is_a_502_with_its_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -1, "msg": "SQL error"})

    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.siyuan_blocks(token="t", client=fake_client(handler))

    assert caught.value.status == 502
    assert "SQL error" in str(caught.value)


def test_an_unreachable_siyuan_is_a_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(memory_routes.MemoryRouteError) as caught:
        memory_routes.siyuan_blocks(token="t", client=fake_client(handler))

    assert caught.value.status == 502
    assert "not reachable" in str(caught.value)
