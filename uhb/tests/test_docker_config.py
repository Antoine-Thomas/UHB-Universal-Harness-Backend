"""The Docker config is read, applied, and resolves the shipped catalog.

``config.docker.yaml`` declares a billed default, a local fallback, and
``allow_paid: false``. Three things have to hold: the two routing keys are
parsed instead of ignored, a billed model is refused when paying is forbidden,
and the provider table resolves every entry of ``catalog.json`` — a container
missing a provider boots with no routing policy at all, which is the failure
this file pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uhb.core.router import InvalidRouting, PaidModelRefused, Router
from uhb.hub.router import clear_cache, load_catalog

DOCKER = Path(__file__).resolve().parents[2] / "config.docker.yaml"


@pytest.fixture(autouse=True)
def _fresh():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def docker() -> Router:
    return Router.from_config(DOCKER)


# --- the two routing keys are read ----------------------------------------


def test_the_docker_config_declares_its_fallback_and_its_cost_policy(docker: Router):
    assert docker.allow_paid is False
    assert docker.fallbacks == ("ollama/qwen3:8b",)


def test_the_docker_policy_refuses_the_billed_default(docker: Router):
    chosen = docker.route_model({"capability": "fast"})

    assert chosen.ref == "ollama/qwen3:8b", "deepseek-flash is billed: the fallback answers"
    assert docker.is_paid("deepseek/deepseek-flash") is True
    assert docker.is_paid("ollama/qwen3:8b") is False


def test_the_docker_chain_keeps_only_what_it_may_spend(docker: Router):
    chain = docker.route_chain({"capability": "fast"})

    assert [ref.ref for ref in chain] == ["ollama/qwen3:8b"]


def test_allowing_paid_restores_the_billed_default():
    router = Router.from_mapping(
        {
            "providers": {
                "deepseek": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
                "ollama": {
                    "protocol": "openai",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key_env": "OLLAMA_API_KEY",
                },
            },
            "routing": {
                "default": "deepseek/deepseek-flash",
                "fallback": "ollama/qwen3:8b",
                "allow_paid": True,
            },
        }
    )

    assert router.route_model({}).ref == "deepseek/deepseek-flash"
    assert [ref.ref for ref in router.route_chain({})] == [
        "deepseek/deepseek-flash",
        "ollama/qwen3:8b",
    ]


def test_a_billed_default_with_no_free_fallback_is_refused():
    router = Router.from_mapping(
        {
            "providers": {
                "deepseek": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key_env": "DEEPSEEK_API_KEY",
                }
            },
            "routing": {"default": "deepseek/deepseek-flash", "allow_paid": False},
        }
    )

    with pytest.raises(PaidModelRefused) as caught:
        router.route_model({})

    assert "allow_paid" in str(caught.value)


def test_a_fallback_nothing_serves_is_refused_at_load():
    with pytest.raises(Exception) as caught:
        Router.from_mapping({"routing": {"fallback": "ghost/model"}})

    assert "ghost/model" in str(caught.value)


def test_a_malformed_allow_paid_is_refused():
    with pytest.raises(InvalidRouting):
        Router.from_mapping({"routing": {"allow_paid": "no"}})


def test_a_fallback_may_be_a_list():
    router = Router.from_mapping(
        {"routing": {"default": "ollama/qwen3:8b", "fallback": ["ollama/llama3.3:70b"]}}
    )

    assert router.fallbacks == ("ollama/llama3.3:70b",)


# --- the catalog boots under the Docker table -----------------------------


def test_the_docker_table_resolves_every_catalog_entry(docker: Router):
    """The measured regression: only deepseek+ollama meant no policy at boot."""
    catalog = load_catalog(router=docker)

    assert len(catalog.candidates) == 11
    for candidate in catalog.candidates:
        descriptor = docker.resolve_provider(candidate.ref)
        assert descriptor.base_url.startswith("http")
        assert descriptor.api_key_env.endswith("_KEY")


def test_the_docker_table_declares_the_free_providers(docker: Router):
    for provider in ("nous", "openrouter"):
        assert provider in docker.providers
        assert docker.providers[provider]["api_key_env"] == f"{provider.upper()}_API_KEY"
