import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


_PROVIDER_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MODELSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
]


def _clear_provider_env(monkeypatch):
    for env_name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)


def test_provider_key_detection_uses_resolved_model_env(monkeypatch):
    from app.models.provider import llm_provider

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")

    assert llm_provider.has_api_key("Qwen/Qwen3-Coder-30B-A3B-Instruct") is True
    assert llm_provider.has_any_api_key() is True


def test_provider_key_detection_rejects_placeholder(monkeypatch):
    from app.models.provider import llm_provider

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-your-api-key-here")

    assert llm_provider.has_api_key("gpt-5.4") is False


def test_provider_key_detection_does_not_reuse_openai_key_for_modelscope(monkeypatch):
    from app.models.provider import llm_provider

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    assert llm_provider.has_api_key("gpt-5.4") is True
    assert llm_provider.has_api_key("Qwen/Qwen3-Coder-30B-A3B-Instruct") is False


def test_fallback_models_only_include_keyed_providers(monkeypatch):
    from app.models.provider import llm_provider

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    candidates = llm_provider.get_fallback_model_names("gpt-5.4")

    assert candidates == ["gpt-5.4"]


def test_fallback_models_include_modelscope_when_keyed(monkeypatch):
    from app.models.provider import llm_provider

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")

    candidates = llm_provider.get_fallback_model_names("Qwen/Qwen3-Coder-30B-A3B-Instruct")

    assert "Qwen/Qwen3-Coder-30B-A3B-Instruct" in candidates
    assert "deepseek-ai/DeepSeek-V3.2" in candidates


def test_readiness_accepts_any_supported_provider_key(monkeypatch):
    import app.main as main

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")

    with TestClient(main.app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["checks"]["api_key"] is True


def test_super_agent_accepts_model_specific_key(monkeypatch):
    from app.agents.super_agent import super_agent

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")

    async def fake_flow(*_args, **_kwargs):
        yield json.dumps({"type": "done"})

    with patch.object(super_agent, "_standard_flow", fake_flow):
        with patch("app.agents.super_agent.cost_tracker.start_tracking"):
            events = asyncio.run(_collect_events(super_agent.handle_message(
                message="hello",
                thread_messages=[],
                model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
            )))

    assert events == [{"type": "done"}]


def test_super_agent_fires_user_prompt_submit_hooks(monkeypatch):
    from app.agents.hooks import HookDefinition, HookHandler, hooks_registry
    from app.agents.super_agent import super_agent

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")
    hook_name = "test_prompt_submit_chat_hook"
    hooks_registry.unregister(hook_name)
    ok, message = hooks_registry.register(HookDefinition(
        event="UserPromptSubmit",
        name=hook_name,
        handlers=[HookHandler(handler_type="command", command="printf prompt-submit")],
    ))
    assert ok, message

    async def fake_flow(*_args, **_kwargs):
        yield json.dumps({"type": "done"})

    try:
        with patch.object(super_agent, "_standard_flow", fake_flow):
            with patch("app.agents.super_agent.cost_tracker.start_tracking"):
                events = asyncio.run(_collect_events(super_agent.handle_message(
                    message="hello",
                    thread_messages=[],
                    model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
                )))
        history = hooks_registry.get_history(limit=20)
        assert events == [{"type": "done"}]
        assert any(item["hook"] == hook_name and item["event"] == "UserPromptSubmit" for item in history)
    finally:
        hooks_registry.unregister(hook_name)


def test_local_agent_accepts_model_specific_key(monkeypatch):
    from app.local.agent import LocalAgent

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "test-modelscope-key")

    async def fake_context(*_args, **_kwargs):
        return ""

    with patch("app.local.agent.memory_store.get_context_for_query", new=AsyncMock(side_effect=fake_context)):
        with patch("app.local.gateway.local_gateway.get_client_for_thread", return_value=None):
            events = asyncio.run(_collect_events(LocalAgent().handle_message(
                message="hello",
                thread_messages=[],
                model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
            )))

    assert events[0]["type"] == "token"
    assert "没有连接到本地客户端" in events[0]["content"]


async def _collect_events(generator):
    events = []
    async for event in generator:
        events.append(json.loads(event))
    return events
