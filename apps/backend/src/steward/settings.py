"""Process settings, read from environment variables (or a local .env file).

Secrets are typed as SecretStr so they never show up in reprs or logs by accident.
Preferences (strategy, schedules, models, budgets) do NOT belong here: they are
versioned config records in the database. This module holds only what a process
needs to start and to reach its dependencies.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"

    # Postgres, connected as the least-privilege `app_backend` role over a direct connection.
    database_url: SecretStr | None = None

    # Supabase Auth: the API verifies the frontend's JWT against the project's JWKS.
    supabase_url: str | None = None
    # Only for projects still on the legacy shared-secret (HS256) JWT signing.
    supabase_jwt_secret: SecretStr | None = None
    # The single owner's auth user id. Tokens for any other user are rejected.
    owner_user_id: str | None = None
    # Require an MFA-verified session (aal2) on every authenticated API call.
    require_mfa: bool = True

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Trading212 (read-only key: enable read scopes only when creating it).
    t212_env: Literal["demo", "live"] = "demo"
    t212_api_key: SecretStr | None = None
    t212_api_secret: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("supabase_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    def secret_values(self) -> list[str]:
        """Every configured secret, for the log scrubber."""
        secrets = [
            self.database_url,
            self.supabase_jwt_secret,
            self.t212_api_key,
            self.t212_api_secret,
        ]
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
