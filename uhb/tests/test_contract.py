"""The contract surface: eight functions, their types, and adapter defaults."""

from __future__ import annotations

import asyncio

import pytest

from uhb.adapters.base import UHBAdapter, UnsupportedFunction
from uhb.adapters.hermes import HermesAdapter
from uhb.core.contract import FUNCTIONS, UHBContract
from uhb.core.health import Health
from uhb.core.router import Router
from uhb.core.types import (
    CancelResult,
    ModelRef,
    StreamEvent,
    TaskContext,
)


def test_eight_pivot_functions_are_declared():
    assert len(FUNCTIONS) == 8
    for name in FUNCTIONS:
        assert hasattr(UHBContract, name), name


def test_contract_is_runtime_checkable():
    adapter = HermesAdapter()
    assert isinstance(adapter, UHBContract)


def test_task_context_roundtrip():
    context = TaskContext(workspace="/w", project="p", topic="t", model_ref="deepseek/x")
    data = context.to_dict()
    assert data["workspace"] == "/w"
    assert data["model_ref"] == "deepseek/x"
    assert data["metadata"] == {}


def test_stream_event_roundtrip():
    event = StreamEvent(type="token", content="hi", metadata={"n": 1})
    assert event.to_dict() == {"type": "token", "content": "hi", "metadata": {"n": 1}}


def test_model_ref_parse_and_ref():
    ref = ModelRef.parse("deepseek/deepseek-v4-pro")
    assert (ref.provider, ref.model_id, ref.ref) == ("deepseek", "deepseek-v4-pro", "deepseek/deepseek-v4-pro")


def test_model_ref_parse_rejects_bare_name():
    with pytest.raises(ValueError):
        ModelRef.parse("deepseek-v4-pro")


def test_base_adapter_refuses_instead_of_lying():
    class Minimal(UHBAdapter):
        def execute_task(self, prompt, context):
            raise UnsupportedFunction("nope")

        async def sync_memory(self, level, content):
            raise UnsupportedFunction("nope")

        async def load_skill(self, skill_id):
            raise UnsupportedFunction("nope")

        def resolve_provider(self, model_ref):
            raise UnsupportedFunction("nope")

    adapter = Minimal()
    with pytest.raises(UnsupportedFunction):
        asyncio.run(adapter.route_model({}))
    with pytest.raises(UnsupportedFunction):
        asyncio.run(adapter.health())
    assert adapter.stream(StreamEvent(type="token")) is None


def test_base_adapter_cancel_is_idempotent_for_unknown_task():
    adapter = HermesAdapter()
    first = asyncio.run(adapter.cancel("does-not-exist"))
    second = asyncio.run(adapter.cancel("does-not-exist"))
    assert first == second == CancelResult(cancelled=False, task_id="does-not-exist", reason="unknown_task")


def test_capabilities_reports_every_function():
    caps = HermesAdapter().capabilities()
    assert set(caps) == set(FUNCTIONS)
    assert caps["execute_task"] is True
    assert caps["stream"] is False  # base no-op, not overridden yet


def test_health_is_down_when_workspace_and_providers_are_missing(tmp_path):
    router = Router(providers={"dead": {"protocol": "openai", "base_url": "http://127.0.0.1:9/v1",
                                       "api_key_env": "X_KEY", "models": []}})
    status = asyncio.run(Health(router=router, workspace=tmp_path / "absent").health())
    assert status.status == "down"
    assert status.checks["workspace"]["ok"] is False
    assert status.checks["dead"]["reachable"] is False
