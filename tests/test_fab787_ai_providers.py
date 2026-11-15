import asyncio

import pytest

from mission_control.fab787_ai_providers import (
    AIProviderError,
    AIProviderManager,
)


def test_default_manager_is_fail_closed_and_never_calls_transport():
    calls = []

    def transport(*args):
        calls.append(args)
        return {}

    manager = AIProviderManager(transport=transport)
    status = manager.status()
    assert status["api_calls_enabled"] is False
    assert status["network_requests_triggered"] == 0
    assert all(not item["api_calls_enabled"] for item in status["providers"])
    with pytest.raises(AIProviderError, match="disabled"):
        asyncio.run(manager.complete("claude", "Analyze this"))
    assert calls == []


def test_environment_switch_alone_cannot_enable_calls():
    manager = AIProviderManager(
        environ={
            "FAB787_AI_API_CALLS_ENABLED": "true",
            "ANTHROPIC_API_KEY": "test-secret",
        },
        transport=lambda *args: {},
    )
    assert manager.api_calls_enabled is False
    with pytest.raises(AIProviderError, match="disabled"):
        asyncio.run(manager.complete("claude", "Analyze this"))


@pytest.mark.parametrize(
    ("provider", "environment", "response", "expected_endpoint", "expected_header"),
    [
        (
            "claude",
            {"ANTHROPIC_API_KEY": "claude-test", "ANTHROPIC_MODEL": "claude-test-model"},
            {"content": [{"type": "text", "text": "Claude research"}]},
            "https://api.anthropic.com/v1/messages",
            "x-api-key",
        ),
        (
            "chatgpt",
            {"OPENAI_API_KEY": "openai-test", "OPENAI_MODEL": "openai-test-model"},
            {"output": [{"content": [{"type": "output_text", "text": "OpenAI research"}]}]},
            "https://api.openai.com/v1/responses",
            "Authorization",
        ),
    ],
)
def test_explicit_opt_in_uses_shared_transport_without_leaking_credentials(
    provider, environment, response, expected_endpoint, expected_header
):
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        return response

    manager = AIProviderManager(
        allow_api_calls=True,
        transport=transport,
        environ={"FAB787_AI_API_CALLS_ENABLED": "true", **environment},
    )
    result = asyncio.run(manager.complete(provider, "Review cached context"))
    assert result["text"] in {"Claude research", "OpenAI research"}
    assert result["provider"] == provider
    assert result["trade_authority"] == "none"
    assert result["execution"] == "disabled"
    assert len(calls) == 1
    url, headers, payload, timeout = calls[0]
    assert url == expected_endpoint
    assert expected_header in headers
    assert timeout <= 60
    assert payload["model"].endswith("test-model")
    assert "test-secret" not in repr(result)


def test_explicit_opt_in_still_requires_credentials():
    manager = AIProviderManager(
        allow_api_calls=True,
        environ={"FAB787_AI_API_CALLS_ENABLED": "true"},
        transport=lambda *args: {},
    )
    with pytest.raises(AIProviderError, match="credentials"):
        asyncio.run(manager.complete("chatgpt", "Review cached context"))


def test_invalid_provider_and_prompt_are_rejected():
    manager = AIProviderManager()
    with pytest.raises(ValueError, match="Provider"):
        manager.provider_config("gemini")
    with pytest.raises(AIProviderError, match="disabled"):
        asyncio.run(manager.complete("claude", ""))
