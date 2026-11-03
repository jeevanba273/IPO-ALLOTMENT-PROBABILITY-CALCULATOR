"""Application settings.

Secrets come only from environment variables (in development, optionally from
the git-ignored .env file at the repo root). They are held as SecretStr so they
never show up in reprs, logs or tracebacks; unwrap one with require() at the
point of use.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Real ipoalerts free-plan limits. Our own caps must stay below them.
IPOALERTS_PLAN_DAILY_LIMIT = 25
IPOALERTS_PLAN_MONTHLY_LIMIT = 750

# Environment variable -> Settings field, for every secret.
SECRET_ENV_VARS = {
    "INDIANAPI_KEY": "indianapi_key",
    "IPOALERTS_KEY": "ipoalerts_key",
    "DATABASE_URL": "database_url",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "DASHBOARD_PASSWORD_HASH": "dashboard_password_hash",
}


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def require(value: SecretStr | None, env_var: str) -> str:
    """Unwrap a secret, failing with the variable's name (never its value) if unset."""
    if value is None:
        raise ConfigError(f"{env_var} is not set")
    return value.get_secret_value()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets
    indianapi_key: SecretStr | None = None
    ipoalerts_key: SecretStr | None = None
    database_url: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    # Dashboard sign-in (the /login page), as a hash from `ipo-intel hash-password`.
    # Required to serve beyond this machine.
    dashboard_password_hash: SecretStr | None = None
    dashboard_user: str = "admin"
    # Where the dashboard is reachable, for links in Telegram alerts.
    dashboard_url: str | None = None

    # External APIs
    indianapi_base_url: str = "https://dev.indianapi.in"
    ipoalerts_base_url: str = "https://api.ipoalerts.in"
    telegram_base_url: str = "https://api.telegram.org"

    # HTTP client wrapper
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    http_max_retries: int = Field(default=3, ge=0, le=5)
    http_backoff_base_seconds: float = Field(default=1.0, ge=0)
    http_backoff_max_seconds: float = Field(default=30.0, ge=0)

    # ipoalerts call budget, per IST day and IST calendar month
    ipoalerts_daily_cap: int = Field(default=12, ge=0)
    ipoalerts_monthly_cap: int = Field(default=400, ge=0)
    ipoalerts_backfill_daily_cap: int = Field(default=8, ge=0)

    # IndianAPI remaining-request thresholds (from GET /usage)
    indianapi_pause_backfill_below: int = Field(default=1000, ge=0)
    indianapi_essential_only_below: int = Field(default=300, ge=0)

    # IndianAPI ingestion
    indianapi_backfill_limit: int = Field(default=50, ge=1, le=500)
    indianapi_backfill_pace_seconds: float = Field(default=3.0, ge=0, le=60)
    # An IPO with no bidding dates this many days after we first saw it is dormant.
    dormant_after_days: int = Field(default=30, ge=1)

    migrations_dir: Path = REPO_ROOT / "migrations"
    # Assumptions of the probability engine (categories, odds, EV, projections).
    rules_path: Path = REPO_ROOT / "config" / "rules_v1.yaml"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator(*SECRET_ENV_VARS.values(), mode="before")
    @classmethod
    def _strip_secret(cls, value: object) -> object:
        # A stray space or newline in a key breaks auth; an empty value means unset.
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_limits(self) -> Settings:
        if self.ipoalerts_daily_cap >= IPOALERTS_PLAN_DAILY_LIMIT:
            raise ValueError(
                f"IPOALERTS_DAILY_CAP must stay below the plan limit of {IPOALERTS_PLAN_DAILY_LIMIT}/day"
            )
        if self.ipoalerts_monthly_cap >= IPOALERTS_PLAN_MONTHLY_LIMIT:
            raise ValueError(
                f"IPOALERTS_MONTHLY_CAP must stay below the plan limit of {IPOALERTS_PLAN_MONTHLY_LIMIT}/month"
            )
        if self.ipoalerts_backfill_daily_cap > self.ipoalerts_daily_cap:
            raise ValueError("IPOALERTS_BACKFILL_DAILY_CAP cannot exceed IPOALERTS_DAILY_CAP")
        if self.indianapi_essential_only_below > self.indianapi_pause_backfill_below:
            raise ValueError(
                "INDIANAPI_ESSENTIAL_ONLY_BELOW cannot exceed INDIANAPI_PAUSE_BACKFILL_BELOW"
            )
        if self.http_backoff_max_seconds < self.http_backoff_base_seconds:
            raise ValueError("HTTP_BACKOFF_MAX_SECONDS cannot be below HTTP_BACKOFF_BASE_SECONDS")
        return self

    def secret_status(self) -> dict[str, bool]:
        """Which secrets are set, by environment variable name. Never the values."""
        return {env: getattr(self, field) is not None for env, field in SECRET_ENV_VARS.items()}

    def secret_values(self) -> list[str]:
        """Raw secret strings (plus the database password) for log redaction."""
        values = [
            secret.get_secret_value()
            for secret in (getattr(self, field) for field in SECRET_ENV_VARS.values())
            if secret is not None
        ]
        if self.database_url is not None:
            try:
                password = conninfo_to_dict(self.database_url.get_secret_value()).get("password")
            except ProgrammingError:
                password = None
            if password:
                values.append(str(password))
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
