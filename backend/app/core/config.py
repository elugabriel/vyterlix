from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be set via a VYTERLIX_* env var or backend/.env."""

    model_config = SettingsConfigDict(env_prefix="VYTERLIX_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_name: str = "Vyterlix API"
    version: str = "0.1.0"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://vyterlix:vyterlix@localhost:5432/vyterlix"
    # JSON list in env, e.g. VYTERLIX_CORS_ORIGINS='["https://app.vyterlix.com"]'
    cors_origins: list[str] = ["http://localhost:5500", "http://127.0.0.1:5500"]
    # Where links in emails point (verify email, reset password, accept invite).
    frontend_base_url: str = "http://localhost:5500"

    # "console" writes emails to the log instead of sending them. Dev/test only.
    email_backend: Literal["console"] = "console"
    email_from: str = "Vyterlix <no-reply@vyterlix.com>"
    email_verification_ttl_hours: int = 24
    # Minimum gap between "send me another link" emails to the same account.
    token_resend_cooldown_seconds: int = 60

    @model_validator(mode="after")
    def _no_wildcard_cors_outside_dev(self) -> "Settings":
        if self.env in ("staging", "prod") and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origin is not allowed in staging/prod")
        return self

    @model_validator(mode="after")
    def _no_console_email_in_prod(self) -> "Settings":
        # The console backend logs live tokens; that must never happen in production.
        if self.env == "prod" and self.email_backend == "console":
            raise ValueError("Console email backend is not allowed in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
