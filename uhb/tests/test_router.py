"""route_model and resolve_provider across the five shipped providers."""

from __future__ import annotations

import pytest

from uhb.core.router import DEFAULT_PROVIDERS, Router, UnknownModelRef


@pytest.fixture()
def router() -> Router:
    return Router()


@pytest.mark.parametrize(
    "model_ref,protocol,base_url,env",
    [
        ("deepseek/deepseek-v4-pro", "deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        ("anthropic/claude-sonnet-5", "anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
        ("openai/gpt-5", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        ("kimi/moonshot-v1-128k", "openai", "https://api.moonshot.cn/v1", "KIMI_API_KEY"),
        ("ollama/qwen3:8b", "openai", "http://127.0.0.1:11434/v1", "OLLAMA_API_KEY"),
    ],
)
def test_resolve_provider_covers_five_providers(router, model_ref, protocol, base_url, env):
    descriptor = router.resolve_provider(model_ref)
    assert descriptor.protocol == protocol
    assert descriptor.base_url == base_url
    assert descriptor.api_key_env == env
    assert descriptor.model_id == model_ref.split("/", 1)[1]


def test_api_key_env_is_a_name_never_a_value(router):
    descriptor = router.resolve_provider("deepseek/deepseek-v4-pro")
    assert descriptor.api_key_env.endswith("_KEY")
    assert " " not in descriptor.api_key_env


def test_unknown_provider_raises(router):
    with pytest.raises(UnknownModelRef):
        router.resolve_provider("nope/model")


def test_missing_model_part_raises(router):
    with pytest.raises(UnknownModelRef):
        router.resolve_provider("deepseek")


def test_route_model_prefers_fast_capability(router):
    assert router.route_model({"capability": "fast"}).ref == "deepseek/deepseek-flash"


def test_route_model_matches_vision(router):
    assert router.route_model({"capability": "vision"}).ref == "anthropic/claude-sonnet-5"


def test_route_model_matches_locality(router):
    assert router.route_model({"locality": "local"}).ref == "ollama/qwen3:8b"


def test_route_model_falls_back_to_default(router):
    assert router.route_model({}).ref == "deepseek/deepseek-v4-pro"
    assert router.route_model({"capability": "unknown"}).ref == "deepseek/deepseek-v4-pro"


def test_route_model_is_deterministic(router):
    intent = {"capability": "code", "min_context": 128000}
    assert router.route_model(intent).ref == router.route_model(intent).ref


def test_route_model_respects_min_context(router):
    ref = router.route_model({"capability": "code", "min_context": 200000})
    assert ref.provider == "deepseek"


def test_custom_config_mapping_wins():
    router = Router.from_mapping({
        "providers": {"local": {"protocol": "openai", "base_url": "http://127.0.0.1:8000/v1",
                                "api_key_env": "LOCAL_KEY", "models": ["m1"]}},
        "routing": {"default": "local/m1", "rules": []},
    })
    assert router.route_model({}).ref == "local/m1"
    assert router.resolve_provider("local/m1").model_id == "m1"


def test_default_table_is_not_mutated_by_a_router_instance(router):
    router.providers.pop("deepseek", None)
    assert "deepseek" in DEFAULT_PROVIDERS
