"""Shared, fail-closed Claude and ChatGPT provider manager.

No network calls occur unless BOTH the caller opts in and the explicit
FAB787_AI_API_CALLS_ENABLED environment switch is set to "true".
This module has no trading, risk-configuration, approval, or order authority.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

PROVIDER_ENV = {
    "claude": {
        "name": "Claude",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-3-5-haiku-latest",
        "endpoint": "https://api.anthropic.com/v1/messages",
    },
    "chatgpt": {
        "name": "ChatGPT",
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4.1-mini",
        "endpoint": "https://api.openai.com/v1/responses",
    },
}
MAX_PROMPT_CHARS = 20_000
MAX_SYSTEM_CHARS = 4_000
MAX_OUTPUT_TOKENS = 2_000
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 60.0


class AIProviderError(RuntimeError):
    """Safe, provider-scoped error that never includes credentials."""


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    name: str
    model: str
    configured: bool
    api_calls_enabled: bool


def _enabled(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() == "true"


def _http_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        # Do not include response bodies: providers may echo submitted content.
        raise AIProviderError(f"Provider returned HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AIProviderError("Provider request failed or timed out.") from None
    if len(raw) > 1_000_000:
        raise AIProviderError("Provider response exceeded the 1 MB limit.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AIProviderError("Provider returned invalid JSON.") from None
    if not isinstance(data, dict):
        raise AIProviderError("Provider returned an unexpected response.")
    return data


class AIProviderManager:
    """One shared, explicitly gated interface for Anthropic and OpenAI."""

    def __init__(
        self,
        *,
        allow_api_calls: bool = False,
        transport: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._allow_api_calls = allow_api_calls is True
        self._transport = transport or _http_json
        self._timeout = min(max(float(timeout_seconds), 1.0), MAX_TIMEOUT_SECONDS)
        self._environ = os.environ if environ is None else environ

    @property
    def api_calls_enabled(self) -> bool:
        return self._allow_api_calls and _enabled(
            self._environ.get("FAB787_AI_API_CALLS_ENABLED")
        )

    def provider_config(self, provider: str) -> ProviderConfig:
        key = provider.strip().lower() if isinstance(provider, str) else ""
        spec = PROVIDER_ENV.get(key)
        if spec is None:
            raise ValueError("Provider must be claude or chatgpt.")
        configured = bool(self._environ.get(spec["key_env"], "").strip())
        return ProviderConfig(
            provider_id=key,
            name=spec["name"],
            model=self._environ.get(spec["model_env"], spec["default_model"]).strip()
            or spec["default_model"],
            configured=configured,
            api_calls_enabled=self.api_calls_enabled and configured,
        )

    def status(self) -> dict[str, Any]:
        providers = []
        for provider in PROVIDER_ENV:
            config = self.provider_config(provider)
            providers.append({
                "id": config.provider_id,
                "name": config.name,
                "configured": config.configured,
                "connected": False,
                "model": config.model,
                "api_calls_enabled": config.api_calls_enabled,
                "status": (
                    "ready_when_explicitly_enabled" if config.api_calls_enabled
                    else "configured_but_disabled" if config.configured
                    else "missing_credentials"
                ),
            })
        return {
            "mode": "shared_provider_manager",
            "api_calls_enabled": self.api_calls_enabled,
            "providers": providers,
            "network_requests_triggered": 0,
            "market_data_requests_triggered": 0,
            "risk_authority": "none",
            "trade_authority": "none",
            "execution": "disabled",
            "human_approval_required": True,
        }

    async def complete(
        self,
        provider: str,
        prompt: str,
        *,
        system: str = (
            "You are a research assistant. Provide source-aware analysis only. "
            "You have no authority to create, approve, modify, or execute trades, "
            "change risk settings, or request market data. State uncertainty clearly."
        ),
        max_output_tokens: int = 800,
    ) -> dict[str, Any]:
        config = self.provider_config(provider)
        if not self.api_calls_enabled:
            raise AIProviderError("AI provider API calls are disabled by default.")
        if not config.configured:
            raise AIProviderError(f"{config.name} API credentials are not configured.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError("Prompt must contain 1 to 20000 characters.")
        if not isinstance(system, str) or not system.strip() or len(system) > MAX_SYSTEM_CHARS:
            raise ValueError("System instructions must contain 1 to 4000 characters.")
        if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError("max_output_tokens must be between 1 and 2000.")

        spec = PROVIDER_ENV[config.provider_id]
        api_key = self._environ[spec["key_env"]].strip()
        if config.provider_id == "claude":
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            payload = {
                "model": config.model,
                "max_tokens": max_output_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {
                "model": config.model,
                "instructions": system,
                "input": prompt,
                "max_output_tokens": max_output_tokens,
                "store": False,
            }

        try:
            response = await asyncio.to_thread(
                self._transport, spec["endpoint"], headers, payload, self._timeout
            )
        except AIProviderError:
            raise
        except Exception:
            raise AIProviderError("Provider transport failed.") from None

        if config.provider_id == "claude":
            blocks = response.get("content", [])
            output = "\n".join(
                item.get("text", "") for item in blocks
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            output_parts = []
            for item in response.get("output", []):
                if not isinstance(item, dict):
                    continue
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        output_parts.append(part.get("text", ""))
            output = "\n".join(output_parts)
        if not isinstance(output, str) or not output.strip():
            raise AIProviderError("Provider response contained no usable text.")
        return {
            "provider": config.provider_id,
            "provider_name": config.name,
            "model": config.model,
            "text": output[:20_000],
            "network_requests_triggered": 1,
            "market_data_requests_triggered": 0,
            "risk_authority": "none",
            "trade_authority": "none",
            "execution": "disabled",
            "human_approval_required": True,
        }


# Shared singleton remains fail-closed. Callers must explicitly construct an
# opted-in manager as well as set the environment switch before any API request.
ai_provider_manager = AIProviderManager()
