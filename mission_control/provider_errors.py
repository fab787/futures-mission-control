"""Provider-specific failures used across data adapters and request scheduling."""


class ProviderQuotaExceeded(RuntimeError):
    """A market-data provider rejected a request because of a quota or rate limit."""


class TradingViewQuotaExceeded(ProviderQuotaExceeded):
    """TradingView rejected a request because a usage quota was exceeded."""


class ProviderCooldownActive(RuntimeError):
    """The provider is in cooldown after a quota or rate-limit response."""


def is_quota_error(error: object) -> bool:
    """Recognize provider quota failures from payloads or transport exceptions."""
    import re

    candidates = [error, getattr(error, "response", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        for attribute in ("status_code", "status"):
            if getattr(candidate, attribute, None) == 429:
                return True
        if isinstance(candidate, dict):
            if candidate.get("status_code") == 429 or candidate.get("status") == 429:
                return True

    message = str(error).lower()
    if isinstance(error, (dict, list, tuple)):
        message += " " + repr(error).lower()
    markers = ("quota", "rate limit", "too many requests", "resource exhausted")
    return any(marker in message for marker in markers) or bool(
        re.search(r"\b(?:http\s*)?429\b", message)
    )
