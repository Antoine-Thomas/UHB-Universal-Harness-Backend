"""``route_model`` and ``resolve_provider`` (SPEC.md §2.4 and §2.8).

The provider table lives in ``uhb/config.yaml`` (git-ignored); ``config.example.yaml``
is the documented default. Only environment variable NAMES appear in either file —
the router never reads a key, and ``ProviderDescriptor.api_key_env`` is returned as
a name for the caller to resolve at call time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .types import ModelRef, ProviderDescriptor

CONFIG_ENV = "UHB_CONFIG"

# Mirrors config.example.yaml. Kept in code so the package works with no file at
# all: a control plane must be able to boot before anyone writes a config.
DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "protocol": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-v4-pro", "deepseek-flash"],
    },
    "anthropic": {
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-5", "claude-haiku-4-5"],
    },
    "openai": {
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-5", "gpt-5-mini"],
    },
    "kimi": {
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": ["moonshot-v1-128k"],
    },
    "ollama": {
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "models": ["qwen3:8b", "llama3.3:70b"],
    },
}

DEFAULT_ROUTING: dict[str, Any] = {
    "default": "deepseek/deepseek-v4-pro",
    "rules": [
        {"match": {"capability": "fast"}, "model": "deepseek/deepseek-flash"},
        {"match": {"capability": "vision"}, "model": "anthropic/claude-sonnet-5"},
        {"match": {"locality": "local"}, "model": "ollama/qwen3:8b"},
    ],
}


class UnknownModelRef(KeyError):
    """Raised when a model reference cannot be resolved to a provider."""


class Router:
    """Config-driven model resolution. Deterministic for a given config."""

    def __init__(
        self,
        providers: dict[str, dict[str, Any]] | None = None,
        routing: dict[str, Any] | None = None,
    ) -> None:
        self.providers = dict(providers or DEFAULT_PROVIDERS)
        self.routing = dict(routing or DEFAULT_ROUTING)

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_config(cls, path: str | Path | None = None) -> "Router":
        """Load ``uhb/config.yaml`` if present, else the built-in default table."""
        candidates = [Path(path)] if path else []
        candidates.append(Path(__file__).resolve().parents[1] / "config.yaml")
        env_path = os.environ.get(CONFIG_ENV)
        if env_path:
            candidates.insert(0, Path(env_path))
        for candidate in candidates:
            if candidate.is_file():
                return cls.from_mapping(_load_yaml(candidate))
        return cls()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Router":
        return cls(
            providers=data.get("providers") or DEFAULT_PROVIDERS,
            routing=data.get("routing") or DEFAULT_ROUTING,
        )

    # -- the two pivot functions ---------------------------------------------

    def resolve_provider(self, model_ref: str) -> ProviderDescriptor:
        """``deepseek/deepseek-v4-pro`` -> protocol, URL, env name, model id."""
        provider, _, model_id = str(model_ref).partition("/")
        entry = self.providers.get(provider)
        if not model_id or entry is None:
            known = ", ".join(sorted(self.providers))
            raise UnknownModelRef(
                f"unknown model reference {model_ref!r}; known providers: {known}"
            )
        return ProviderDescriptor(
            protocol=str(entry.get("protocol", "openai")),
            base_url=str(entry["base_url"]),
            api_key_env=str(entry["api_key_env"]),
            model_id=model_id,
        )

    def route_model(self, intent: dict[str, Any] | None = None) -> ModelRef:
        """First matching rule wins; ``routing.default`` is the fallback."""
        intent = dict(intent or {})
        for rule in self.routing.get("rules", []):
            if _matches(rule.get("match") or {}, intent):
                ref = rule.get("model")
                if ref:
                    return self._to_ref(ref)
        return self._to_ref(self.routing["default"])

    # -- helpers --------------------------------------------------------------

    def _to_ref(self, raw: str) -> ModelRef:
        model_ref = ModelRef.parse(str(raw))
        entry = self.providers.get(model_ref.provider) or {}
        model_ref.protocol = str(entry.get("protocol", "openai"))  # type: ignore[assignment]
        return model_ref


def _matches(match: dict[str, Any], intent: dict[str, Any]) -> bool:
    for key, expected in match.items():
        if key == "min_context":
            if int(intent.get("min_context") or 0) < int(expected):
                return False
        elif intent.get(key) != expected:
            return False
    return True


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # local import: the package stays importable without PyYAML

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at top level")
    return data
