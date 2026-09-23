from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known, public value: fine for local dev and tests, refused in staging/prod.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"


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
    password_reset_ttl_minutes: int = 60
    # Minimum gap between "send me another link" emails to the same account.
    token_resend_cooldown_seconds: int = 60

    # Sessions. Access tokens are short-lived JWTs kept in page memory; the refresh token
    # lives in an httpOnly cookie and is stored hashed in user_sessions.
    jwt_secret: str = DEV_JWT_SECRET
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    # Browsers treat http://localhost as secure, so this can stay on in dev too.
    cookie_secure: bool = True

    # Failed-login limits within a rolling window.
    login_failure_window_minutes: int = 15
    login_max_failures_per_email: int = 5
    login_max_failures_per_ip: int = 20

    @model_validator(mode="after")
    def _no_wildcard_cors_outside_dev(self) -> "Settings":
        if self.env in ("staging", "prod") and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origin is not allowed in staging/prod")
        return self

    @model_validator(mode="after")
    def _real_jwt_secret_outside_dev(self) -> "Settings":
        if self.env in ("staging", "prod") and (
            self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("Set VYTERLIX_JWT_SECRET to a random value of 32+ characters")
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
