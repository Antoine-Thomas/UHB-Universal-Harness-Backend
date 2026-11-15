"""The free tier: what the catalogue declares, and what the router does with it.

The free models live in ``uhb/hub/router/catalog.json`` and are reached through
the provider table (``NOUS_API_KEY``, ``OPENROUTER_API_KEY``). These tests pin
three things: the catalogue says what it means, a free candidate is tried before
the mode's own tier, and a quota error (429) moves the chain instead of ending it.

No test here reaches the network: the sender is injected, so a "429" is the
exception a real provider call would raise after ``raise_for_status``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uhb.core.router import Router
from uhb.hub.modes import clear_cache as clear_modes, parse_mode
from uhb.hub.router import (
    AllProvidersFailed,
    InvalidCatalog,
    clear_cache,
    complete,
    execute,
    load_catalog,
    route,
)

TRIVIAL = "Ou en est le disque ?"
STANDARD = "Refactor the router and add free-model tests."

#: The free candidates, in chain order. The first one is the one that answers.
FREE = (
    "nous/meituan/longcat-2.5:free",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter/recursive/flint-v1.0:free",
    "nous/upstage/solar-mini4:free",
    "openrouter/google/gemma-4-31b-it:free",
    "nous/stepfun/step-3.7-flash:free",
)

PAID = ("kimi/moonshot-v1-128k", "anthropic/claude-sonnet-5")


class RateLimited(Exception):
    """What a 429 becomes once httpx has raised for the status."""


@pytest.fixture(autouse=True)
def _fresh():
    clear_cache()
    clear_modes()
    yield
    clear_cache()
    clear_modes()


def _catalog() -> dict[str, object]:
    return {candidate.ref: candidate for candidate in load_catalog(router=Router()).candidates}


# --- the catalogue ---------------------------------------------------------


def test_the_catalog_declares_every_free_model_in_the_free_tier():
    catalog = _catalog()

    for ref in FREE:
        assert ref in catalog, f"{ref} is missing from catalog.json"
        assert catalog[ref].tier == "free", ref


def test_the_free_models_declare_their_context_window():
    catalog = _catalog()

    assert catalog["nous/meituan/longcat-2.5:free"].context == 1048576
    assert catalog["openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"].context == 1000000
    assert catalog["nous/upstage/solar-mini4:free"].context == 524288
    assert catalog["openrouter/recursive/flint-v1.0:free"].context == 262144


def test_the_free_models_declare_a_quota_in_requests():
    catalog = _catalog()
    candidate = catalog["nous/meituan/longcat-2.5:free"]

    assert dict(candidate.limits) == {"rpm": 20, "rpd": 50}
    assert candidate.to_dict()["limits"] == {"rpm": 20, "rpd": 50}, (
        "the quota survives the trip to the client"
    )


def test_a_quota_that_is_not_a_positive_integer_is_refused(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "tiers": ["eco", "free", "paid"],
                "models": [
                    {
                        "ref": "deepseek/deepseek-flash",
                        "tier": "eco",
                        "complexity": ["trivial"],
                        "limits": {"rpm": "20"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidCatalog) as caught:
        load_catalog(path, router=Router())

    assert "rpm" in str(caught.value)


def test_a_context_that_is_not_an_integer_is_refused(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "tiers": ["eco", "free", "paid"],
                "models": [
                    {
                        "ref": "deepseek/deepseek-flash",
                        "tier": "eco",
                        "complexity": ["trivial"],
                        "context": "1M",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidCatalog) as caught:
        load_catalog(path, router=Router())

    assert "context" in str(caught.value)


def test_the_free_models_resolve_through_the_provider_table():
    catalog = _catalog()
    table = Router()

    for ref in FREE:
        descriptor = table.resolve_provider(ref)
        assert descriptor.base_url.startswith("http")
        assert descriptor.api_key_env in ("NOUS_API_KEY", "OPENROUTER_API_KEY"), ref
        assert catalog[ref].api_key_env == descriptor.api_key_env


# --- the chain -------------------------------------------------------------


def test_an_eco_mode_tries_the_free_tier_first():
    decision = route(TRIVIAL, "dev")

    assert decision.chosen.ref == FREE[0]
    assert decision.candidates[0].tier == "free"
    assert [candidate.tier for candidate in decision.candidates[: len(FREE)]] == ["free"] * len(FREE)


def test_free_first_never_opens_the_paid_tier():
    decision = route(STANDARD, "dev")

    assert decision.allow_paid is False
    assert not any(candidate.tier == "paid" for candidate in decision.candidates), (
        "a free-first chain still honours allow_paid"
    )


def test_a_free_candidate_without_a_key_is_skipped_not_called(monkeypatch):
    for name in ("NOUS_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def sender(candidate, prompt, max_tokens):  # pragma: no cover - must not run
        raise AssertionError("a candidate with no key must be skipped")

    decision = route(STANDARD, "dev")
    with pytest.raises(AllProvidersFailed):
        execute(decision, "summarise", sender=sender, max_tokens=64)

    assert decision.candidates[0].ref == FREE[0]


def test_complete_answers_from_the_free_tier():
    seen: list[str] = []

    def sender(candidate, prompt, max_tokens):
        seen.append(candidate.ref)
        return "ok"

    result = complete(STANDARD, mode="dev", sender=sender)

    assert result.tier == "free"
    assert result.provider == FREE[0]
    assert seen == [FREE[0]]


# --- quota errors move the chain ------------------------------------------


def test_a_429_on_the_first_free_model_moves_to_the_next():
    def sender(candidate, prompt, max_tokens):
        if candidate.ref == FREE[0]:
            raise RateLimited("429")
        return "second free answered"

    result = execute(route(STANDARD, "dev"), "summarise", sender=sender)

    assert result.provider == FREE[1]
    assert [(attempt.provider, attempt.outcome, attempt.detail) for attempt in result.attempts] == [
        (FREE[0], "failed", "RateLimited"),
        (FREE[1], "ok", ""),
    ]


def test_when_every_free_model_is_rate_limited_the_eco_tier_answers():
    def sender(candidate, prompt, max_tokens):
        if candidate.tier == "free":
            raise RateLimited("429")
        return "eco answered"

    result = execute(route(STANDARD, "dev"), "summarise", sender=sender)

    assert result.tier == "eco"
    assert result.attempts[0].provider == FREE[0]
    assert result.attempts[0].outcome == "failed"


def test_the_paid_tier_is_reached_only_when_the_mode_allows_it():
    strict = parse_mode(
        {"name": "strict", "read": ["**"], "allow_paid": False, "free_first": True,
         "model": {"tier": "eco"}},
        "strict.json",
    )
    paid = parse_mode(
        {"name": "paid", "read": ["**"], "allow_paid": True, "free_first": True,
         "model": {"tier": "eco"}},
        "paid.json",
    )

    def sender(candidate, prompt, max_tokens):
        raise RateLimited("429")

    with pytest.raises(AllProvidersFailed) as caught:
        execute(route(STANDARD, strict), "summarise", sender=sender)
    assert not any(attempt.provider in PAID for attempt in caught.value.attempts), (
        "allow_paid: false must hold even with free_first: true"
    )

    with pytest.raises(AllProvidersFailed) as caught_paid:
        execute(route(STANDARD, paid), "summarise", sender=sender)
    refs = [attempt.provider for attempt in caught_paid.value.attempts]
    assert "deepseek/deepseek-flash" in refs, "the eco tier is the first paid refuge"
    assert any(ref in PAID for ref in refs), "allow_paid: true widens the chain"


def test_a_chain_long_enough_to_keep_the_paid_refuge():
    """A truncated chain that drops the refuge is a silent loss of fallback."""
    paid = parse_mode(
        {"name": "paid", "read": ["**"], "allow_paid": True, "free_first": True,
         "model": {"tier": "eco"}},
        "paid.json",
    )

    decision = route(STANDARD, paid)

    assert decision.candidates[-1].tier in ("eco", "paid")
    assert any(candidate.tier == "paid" for candidate in decision.candidates)
