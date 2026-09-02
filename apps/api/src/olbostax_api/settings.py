"""Application configuration.

Settings come from the environment. In production the values behind them come
from a secret manager, injected as environment variables by the platform, and
never from a file in the repository.

The validators here refuse to start the process when a production deployment
is misconfigured in a way that would be unsafe. Failing at boot is far better
than serving traffic with an ephemeral encryption key or a mock payment
processor: the first is a loud, immediate, fixable problem, the second is
discovered by a taxpayer.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]

Environment = Literal["local", "test", "development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OLBOSTAX_", env_file=".env", extra="ignore"
    )

    env: Environment = "local"

    database_url: str = Field(
        default="postgresql+psycopg://olbostax:olbostax@localhost:5432/olbostax",
        alias="DATABASE_URL",
    )
    database_pool_size: int = 10
    database_echo: bool = False

    session_secret: str = ""
    session_access_ttl_minutes: int = 15
    session_ttl_hours: int = 12
    ip_hash_salt: str = ""

    efile_provider: str = "mock"
    payment_provider: str = "mock"

    filing_price_cents: int = 1499
    """$14.99, the single price. Stored in cents because a price in a float is
    how a checkout ends up charging $14.989999999999998."""

    max_upload_bytes: int = 20 * 1024 * 1024
    allowed_upload_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
    )

    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def price_display(self) -> str:
        dollars, cents = divmod(self.filing_price_cents, 100)
        return f"${dollars}.{cents:02d}"

    @model_validator(mode="after")
    def _database_url_must_be_present(self) -> Settings:
        """Reject an empty database URL in every environment.

        An environment variable exported as the empty string is a value, not an
        absence, so pydantic accepts it and the failure surfaces later as an
        obscure driver error. Failing here names the actual problem.
        """
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL is empty; set it or leave it unset")
        return self

    @model_validator(mode="after")
    def _production_requires_real_configuration(self) -> Settings:
        if not self.is_production:
            return self

        problems: list[str] = []
        if len(self.session_secret) < 32:
            problems.append("OLBOSTAX_SESSION_SECRET must be set to at least 32 characters")
        if len(self.ip_hash_salt) < 16:
            problems.append("OLBOSTAX_IP_HASH_SALT must be set to at least 16 characters")
        if self.payment_provider == "mock":
            problems.append(
                "OLBOSTAX_PAYMENT_PROVIDER is 'mock'; production would accept filings "
                "without taking payment"
            )
        if self.database_echo:
            problems.append(
                "OLBOSTAX_DATABASE_ECHO is on; SQL logging in production would write "
                "taxpayer data to the application log"
            )
        if any(origin.startswith("http://") for origin in self.cors_origins):
            problems.append("CORS origins must use https in production")

        if problems:
            raise ValueError(
                "refusing to start in production with unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self

    @model_validator(mode="after")
    def _development_defaults(self) -> Settings:
        """Fill in throwaway secrets for local development only.

        Deliberately after the production validator, so this can never mask a
        production misconfiguration.
        """
        if self.is_production:
            return self
        import secrets

        if not self.session_secret:
            object.__setattr__(self, "session_secret", secrets.token_hex(32))
        if not self.ip_hash_salt:
            object.__setattr__(self, "ip_hash_salt", secrets.token_hex(16))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
