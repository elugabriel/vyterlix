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

    @model_validator(mode="after")
    def _no_wildcard_cors_outside_dev(self) -> "Settings":
        if self.env in ("staging", "prod") and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origin is not allowed in staging/prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
