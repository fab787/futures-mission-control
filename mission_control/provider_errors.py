"""Provider-specific failures used across data adapters and request scheduling."""


class TradingViewQuotaExceeded(RuntimeError):
    """TradingView rejected a request because a usage quota was exceeded."""


class ProviderCooldownActive(RuntimeError):
    """The provider is in cooldown after a quota or rate-limit response."""
