import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known, public value: fine for local dev and tests, refused in staging/prod.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"
# Same idea for the key that encrypts stored connection credentials (a Fernet key is 32 bytes,
# url-safe base64). Public, so refused in staging/prod.
DEV_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"dev-only-insecure-fernet-key-000").decode()


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
    # "smtp": any provider that offers SMTP (Amazon SES, Postmark, Mailgun, Microsoft...).
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None  # only ever from the environment
    smtp_use_tls: bool = True  # STARTTLS on the port above
    smtp_timeout_seconds: float = 20.0
    email_from: str = "Vyterlix <no-reply@vyterlix.com>"
    email_verification_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 60
    invitation_ttl_days: int = 7
    # Minimum gap between "send me another link" emails to the same account.
    token_resend_cooldown_seconds: int = 60

    # Data imports (decisions 2026-09-28). Uploaded files live outside the repo; in
    # production this becomes UK-hosted storage (L3).
    upload_dir: str = str(Path(__file__).resolve().parents[4] / "vyterlix-uploads")
    max_upload_bytes: int = 25 * 1024 * 1024
    max_upload_rows: int = 250_000
    upload_retention_days: int = 90  # originals deleted after this; imported records stay

    # Background jobs (Phase 4 step 10). Files bigger than this are checked and imported by the
    # worker, never inside a web request.
    max_inline_rows: int = 5_000
    job_stale_after_seconds: int = 900  # a running job silent this long lost its worker
    job_retry_delay_seconds: int = 30  # grows with each attempt
    job_keep_days: int = 30  # finished jobs are pruned after this

    # Connections to other systems (Xero, Shopify...). Their tokens are stored encrypted with
    # this key. To rotate: put the new key first and keep the old ones in `previous_...`; every
    # token is re-encrypted with the new key the next time it is saved.
    encryption_key: str = DEV_ENCRYPTION_KEY
    # The AI assistant answers from the business's own results with fixed rules. An outside provider
    # (Claude) may only reword those answers, and only for a business whose owner has allowed it.
    ai_provider: Literal["offline", "anthropic"] = "offline"
    anthropic_api_key: SecretStr | None = None  # only ever from the environment, never stored
    anthropic_model: str = "claude-sonnet-5-5"
    ai_timeout_seconds: float = 20.0

    # Billing. The sandbox (no money moves) exists in dev and test only. A real provider is used
    # only when its keys are set; every key comes from the environment, never stored or shown.
    trial_days: int = 14
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    paystack_secret_key: SecretStr | None = None
    previous_encryption_keys: list[str] = []
    oauth_state_ttl_minutes: int = 10  # how long a "connect" attempt stays valid
    token_refresh_margin_seconds: int = 120  # refresh access tokens this long before they expire
    # Where a provider sends the person back after they approve access (a frontend page).
    integration_callback_path: str = "/integrations-callback.html"

    # Sessions. Access tokens are short-lived JWTs kept in page memory; the refresh token
    # lives in an httpOnly cookie and is stored hashed in user_sessions.
    jwt_secret: str = DEV_JWT_SECRET
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    # Browsers treat http://localhost as secure, so this can stay on in dev too.
    cookie_secure: bool = True

    # The mobile apps. An app older than the minimum is told to update; the store links are where.
    mobile_min_version: str = "1.0.0"
    mobile_latest_version: str = "1.0.0"
    ios_store_url: str | None = None
    android_store_url: str | None = None

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
    def _real_encryption_key_outside_dev(self) -> "Settings":
        if self.env in ("staging", "prod") and self.encryption_key == DEV_ENCRYPTION_KEY:
            raise ValueError("Set VYTERLIX_ENCRYPTION_KEY (a Fernet key) outside dev")
        return self

    @model_validator(mode="after")
    def _no_console_email_in_prod(self) -> "Settings":
        # The console backend logs live tokens; that must never happen in production.
        if self.env == "prod" and self.email_backend == "console":
            raise ValueError("Console email backend is not allowed in prod")
        return self

    @model_validator(mode="after")
    def _stripe_needs_both_keys(self) -> "Settings":
        if (self.stripe_secret_key is None) != (self.stripe_webhook_secret is None):
            raise ValueError(
                "Set both VYTERLIX_STRIPE_SECRET_KEY and VYTERLIX_STRIPE_WEBHOOK_SECRET"
            )
        return self

    @model_validator(mode="after")
    def _smtp_needs_a_host(self) -> "Settings":
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("Set VYTERLIX_SMTP_HOST to send email through SMTP")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
