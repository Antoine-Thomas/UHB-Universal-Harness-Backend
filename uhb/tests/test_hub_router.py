"""Intent router (Phase 1.3): classification, chain planning, fail-closed fallback.

No test here reaches the network: the sender is injected, or the shipped
``default_sender`` is replaced. The only real numbers are the latencies.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from uhb.core.router import Router
from uhb.hub.modes import clear_cache as clear_modes, get_mode, parse_mode
from uhb.hub.router import (
    AllProvidersFailed,
    InvalidCatalog,
    UnroutableRequest,
    build_request,
    classify,
    clear_cache,
    complete,
    execute,
    has_credentials,
    load_catalog,
    route,
)

TRIVIAL = "Comment fonctionne le routeur ?"
STANDARD = "Refactor the router and add fallback tests."
COMPLEX = (
    "Refactor, migrate and rewrite the executor, debug the streaming path in "
    "uhb/core/executor.py and uhb/core/router.py, then add tests and document it."
)


@pytest.fixture(autouse=True)
def _fresh():
    clear_cache()
    clear_modes()
    yield
    clear_cache()
    clear_modes()


def write_catalog(path: Path, models: list[dict], tiers=("eco", "free", "paid")) -> Path:
    path.write_text(json.dumps({"tiers": list(tiers), "models": models}), encoding="utf-8")
    return path


# --- classification --------------------------------------------------------


def test_a_short_question_is_trivial():
    result = classify(TRIVIAL)

    assert result.complexity == "trivial"
    assert result.score <= 1


def test_multi_step_work_is_standard():
    result = classify(STANDARD)

    assert result.complexity == "standard"
    assert result.signals["multi_step"] >= 2


def test_code_and_multi_step_is_complex():
    result = classify(COMPLEX)

    assert result.complexity == "complex"
    assert result.signals["paths"] == 1
    assert result.signals["multi_step"] == 3


def test_classification_exposes_its_signals_and_is_deterministic():
    first = classify(COMPLEX)
    second = classify(COMPLEX)

    assert first == second
    assert set(first.signals) == {"length", "code", "multi_step", "paths", "multi_question"}


def test_forced_complexity_skips_classification():
    decision = route(TRIVIAL, "chat", complexity="complex")

    assert decision.complexity == "complex"
    assert decision.signals == {"forced": 1}
    assert decision.chosen.ref == "deepseek/deepseek-v4-pro", "flash does not claim complex"


def test_unknown_complexity_is_refused():
    with pytest.raises(UnroutableRequest):
        route(TRIVIAL, "chat", complexity="extreme")


# --- catalog ---------------------------------------------------------------


def test_catalog_covers_three_tiers_and_resolves_through_the_provider_table():
    catalog = load_catalog(router=Router())
    table = Router()

    assert {candidate.tier for candidate in catalog.candidates} == {"eco", "free", "paid"}
    for candidate in catalog.candidates:
        descriptor = table.resolve_provider(candidate.ref)
        assert (candidate.protocol, candidate.base_url, candidate.api_key_env, candidate.model_id) == (
            descriptor.protocol,
            descriptor.base_url,
            descriptor.api_key_env,
            descriptor.model_id,
        )


def test_unknown_tier_in_catalog_is_refused(tmp_path: Path):
    path = write_catalog(tmp_path / "c.json", [{"ref": "deepseek/deepseek-flash", "tier": "platinum", "complexity": ["trivial"]}])

    with pytest.raises(InvalidCatalog) as caught:
        load_catalog(path, router=Router())

    assert "platinum" in str(caught.value)


def test_a_ref_no_provider_can_serve_is_refused(tmp_path: Path):
    path = write_catalog(tmp_path / "c.json", [{"ref": "mistral/mistral-large", "tier": "eco", "complexity": ["trivial"]}])

    with pytest.raises(InvalidCatalog) as caught:
        load_catalog(path, router=Router())

    assert "mistral/mistral-large" in str(caught.value)


def test_catalog_entry_without_complexity_is_refused(tmp_path: Path):
    path = write_catalog(tmp_path / "c.json", [{"ref": "deepseek/deepseek-flash", "tier": "eco"}])

    with pytest.raises(InvalidCatalog):
        load_catalog(path, router=Router())


def test_broken_catalog_json_fails_loudly(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(InvalidCatalog):
        load_catalog(path, router=Router())


# --- planning --------------------------------------------------------------


def test_a_trivial_chat_goes_to_the_cheap_eco_model_first():
    decision = route(TRIVIAL, "chat")

    assert decision.chosen.ref == "deepseek/deepseek-flash"
    assert [candidate.ref for candidate in decision.candidates] == [
        "deepseek/deepseek-flash",
        "deepseek/deepseek-v4-pro",
        "ollama/qwen3:8b",
    ]


def test_a_complex_review_drops_the_model_that_cannot_hold_it():
    decision = route(COMPLEX, "review")

    assert decision.chosen.ref == "deepseek/deepseek-v4-pro"
    assert "deepseek/deepseek-flash" not in [candidate.ref for candidate in decision.candidates]


def test_observe_stays_on_the_free_tier():
    decision = route("Resume l'etat du disque.", "observe")

    assert decision.chosen.ref == "ollama/qwen3:8b"
    assert decision.candidates[0].tier == "free"
    assert all(candidate.tier == "free" for candidate in decision.candidates)


def test_no_shipped_mode_can_reach_a_paid_model():
    paid = []
    for name in ("chat", "dev", "review", "observe"):
        decision = route(COMPLEX, name, complexity="complex") if name == "dev" else route(TRIVIAL, name)
        paid.extend(candidate.ref for candidate in decision.candidates if candidate.tier == "paid")

    assert paid == [], "paid is manual only: no shipped profile sets allow_paid"


def test_a_mode_that_allows_paid_gets_it_in_the_chain():
    research = parse_mode(
        {"name": "research", "read": ["**"], "allow_paid": True, "model": {"tier": "eco"}},
        "research.json",
    )

    trivial = route(TRIVIAL, research)
    complex_ = route(TRIVIAL, research, complexity="complex")

    assert "kimi/moonshot-v1-128k" in [candidate.ref for candidate in trivial.candidates]
    assert "anthropic/claude-sonnet-5" in [candidate.ref for candidate in complex_.candidates]


def test_a_free_tier_mode_cannot_route_a_complex_request():
    with pytest.raises(UnroutableRequest) as caught:
        route(TRIVIAL, "observe", complexity="complex")

    assert "allow_paid" in str(caught.value)
    assert "observe" in str(caught.value)


def test_routing_is_pure():
    first = route(COMPLEX, "dev")
    second = route(COMPLEX, "dev")

    assert first.candidates == second.candidates
    assert first.reason == second.reason
    assert first.allow_paid is False


# --- continuity with the contract-level router -----------------------------


def test_the_hub_router_does_not_fork_the_provider_truth():
    table = Router()
    decision = route(TRIVIAL, "dev")
    chosen = decision.chosen

    descriptor = table.resolve_provider(chosen.ref)

    assert (chosen.base_url, chosen.api_key_env) == (descriptor.base_url, descriptor.api_key_env)
    assert chosen.model_id == "deepseek-flash"


def test_deepseek_flash_is_still_the_fast_capable_default():
    """The HUB default and the contract default must name the same model."""
    table = Router()
    contract = table.route_model({"capability": "fast"})
    hub = route(TRIVIAL, "dev").chosen

    assert contract.ref == "deepseek/deepseek-flash"
    assert hub.ref == contract.ref
    assert hub.base_url == table.resolve_provider(contract.ref).base_url
    assert hub.model_id == "deepseek-flash"


def test_the_contract_router_still_resolves_every_catalog_entry():
    table = Router()
    for name in ("deepseek", "anthropic", "openai", "kimi", "ollama"):
        descriptor = table.resolve_provider(f"{name}/x")
        assert descriptor.base_url.startswith("http")
        assert descriptor.api_key_env.endswith("_KEY")


# --- execution and fallback ------------------------------------------------


def test_the_first_candidate_answers_and_the_chain_stops():
    seen: list[str] = []

    def sender(candidate, prompt, max_tokens):
        seen.append(candidate.ref)
        return f"answer from {candidate.ref}"

    result = execute(route(STANDARD, "dev"), "summarise", sender=sender)

    assert seen == ["deepseek/deepseek-flash"]
    assert result.provider == "deepseek/deepseek-flash"
    assert result.tier == "eco"
    assert [attempt.outcome for attempt in result.attempts] == ["ok"]


def test_a_failing_provider_falls_through_to_the_next():
    def sender(candidate, prompt, max_tokens):
        if candidate.ref == "deepseek/deepseek-flash":
            raise TimeoutError("provider did not answer")
        return "second answered"

    result = execute(route(STANDARD, "dev"), "summarise", sender=sender)

    assert result.text == "second answered"
    assert result.provider == "deepseek/deepseek-v4-pro"
    assert [(attempt.provider, attempt.outcome) for attempt in result.attempts] == [
        ("deepseek/deepseek-flash", "failed"),
        ("deepseek/deepseek-v4-pro", "ok"),
    ]
    assert result.attempts[0].detail == "TimeoutError", "the reason is the class, never a payload"


def test_when_everything_fails_it_raises_instead_of_inventing_an_answer():
    def sender(candidate, prompt, max_tokens):
        raise RuntimeError("down")

    with pytest.raises(AllProvidersFailed) as caught:
        execute(route(STANDARD, "dev"), "summarise", sender=sender)

    assert len(caught.value.attempts) == 3
    assert all(attempt.outcome == "failed" for attempt in caught.value.attempts)


def test_a_remote_provider_without_a_key_is_skipped_not_called(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called: list[str] = []

    def fake_sender(candidate, prompt, max_tokens):
        called.append(candidate.ref)
        return f"local answer from {candidate.ref}"

    monkeypatch.setattr("uhb.hub.router.default_sender", fake_sender)

    result = execute(route(STANDARD, "dev"), "summarise")

    assert called == ["ollama/qwen3:8b"], "only the local provider may be called without a key"
    assert [attempt.outcome for attempt in result.attempts] == ["skipped", "skipped", "ok"]
    assert "DEEPSEEK_API_KEY" in result.attempts[0].detail


def test_credentials_are_detected_by_name_only(monkeypatch):
    catalog = load_catalog(router=Router())
    deepseek = next(c for c in catalog.candidates if c.ref.startswith("deepseek/"))
    ollama = next(c for c in catalog.candidates if c.ref.startswith("ollama/"))

    monkeypatch.delenv(deepseek.api_key_env, raising=False)
    assert has_credentials(deepseek) is False
    assert has_credentials(ollama) is True

    monkeypatch.setenv(deepseek.api_key_env, "placeholder-value-never-logged")
    assert has_credentials(deepseek) is True


def test_complete_routes_then_executes_with_the_mode_token_budget():
    budgets: list[int] = []

    def sender(candidate, prompt, max_tokens):
        budgets.append(max_tokens)
        return "ok"

    result = complete("Refactor this module.", mode="dev", sender=sender)

    assert result.text == "ok"
    assert budgets == [get_mode("dev").max_tokens]


# --- request shape per protocol -------------------------------------------


def test_openai_style_request_shape():
    catalog = load_catalog(router=Router())
    deepseek = next(c for c in catalog.candidates if c.ref.startswith("deepseek/"))

    url, headers, payload = build_request(deepseek, "hello", 512)

    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert payload["model"] == "deepseek-flash"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert "authorization" not in headers, "no credential without one in the environment"


def test_anthropic_style_request_shape():
    catalog = load_catalog(router=Router())
    claude = next(c for c in catalog.candidates if c.ref.startswith("anthropic/"))

    url, headers, payload = build_request(claude, "hello", 512)

    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["anthropic-version"] == "2023-06-01"
    assert payload["max_tokens"] == 512


def test_thinking_is_turned_off_by_default_and_can_be_restored(monkeypatch):
    catalog = load_catalog(router=Router())
    deepseek = next(c for c in catalog.candidates if c.ref.startswith("deepseek/"))

    monkeypatch.delenv("UHB_LLM_REASONING", raising=False)
    _, _, payload = build_request(deepseek, "hello", 512)
    assert payload["reasoning_effort"] == "none"

    monkeypatch.setenv("UHB_LLM_REASONING", "default")
    _, _, payload = build_request(deepseek, "hello", 512)
    assert "reasoning_effort" not in payload, "a provider that rejects the field can be left alone"


# --- measured latency ------------------------------------------------------


def test_route_latency():
    catalog = load_catalog(router=Router())
    mode = get_mode("dev")
    samples: list[float] = []
    for _ in range(300):
        started = time.perf_counter()
        route(STANDARD, mode, catalog=catalog)
        samples.append((time.perf_counter() - started) * 1000.0)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[int(len(samples) * 0.95)]
    print(f"\nroute(): p50={p50:.3f} ms  p95={p95:.3f} ms  max={samples[-1]:.3f} ms (n=300)")

    assert p95 < 5.0, f"p95 {p95:.3f} ms exceeds the 5 ms budget"
    assert sum(samples) / len(samples) < 2.0


def test_cold_load_latency():
    import uhb.hub.router as router_module

    router_module.clear_cache()
    started = time.perf_counter()
    catalog = load_catalog(router=Router())
    cold = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    load_catalog(router=Router())
    warm = (time.perf_counter() - started) * 1000.0
    print(f"\nload_catalog(): cold={cold:.3f} ms  warm={warm:.3f} ms ({len(catalog.candidates)} models)")

    assert warm < 5.0, "a cached catalog must cost less than a millisecond-scale read"
    assert cold < 50.0
