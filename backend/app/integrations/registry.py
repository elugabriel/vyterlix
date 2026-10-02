"""Which providers exist. A provider appears here once its connector is written; whether it can
actually be used also depends on `is_configured` (the app registered with that provider)."""

from app.core.config import get_settings
from app.core.errors import AppError
from app.integrations.base import Provider
from app.integrations.sandbox import SandboxProvider

PROVIDERS: dict[str, Provider] = {p.key: p for p in (SandboxProvider(),)}


def get_provider(key: str) -> Provider:
    provider = PROVIDERS.get(key)
    if provider is None or not provider.is_configured(get_settings()):
        raise AppError(
            "That kind of connection isn't available.", code="unknown_provider", status_code=422
        )
    return provider


def available_providers() -> list[Provider]:
    settings = get_settings()
    return [p for p in PROVIDERS.values() if p.is_configured(settings)]
