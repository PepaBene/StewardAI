"""Process settings, read from environment variables (or the repository-root .env file).

Secrets are typed as SecretStr so they never show up in reprs or logs by accident.
Preferences (strategy, schedules, models, budgets) do NOT belong here: they are
versioned config records in the database. This module holds only what a process
needs to start and to reach its dependencies.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# One .env at the repository root holds every setting for both apps (see /.env.example).
# Real environment variables take precedence; on the live host there is no file at all.
# Outside a repository checkout (a container image) there is no such file; env vars only.
_HERE = Path(__file__).resolve()
REPO_ROOT_ENV_FILE = _HERE.parents[4] / ".env" if len(_HERE.parents) > 4 else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # `KEY=` lines in .env mean "not set", not "empty string".
        env_ignore_empty=True,
    )

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

    # OpenAI (gpt-6.1-sol, gpt-6-luna) and Google (gemini-3.8-flash): see steward.llm.
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    # Supabase Storage writes to the `evidence` bucket (secret or legacy service_role key).
    supabase_secret_key: SecretStr | None = None
    # SEC EDGAR asks for a descriptive User-Agent with a contact address.
    sec_user_agent: str | None = None
    # Finnhub free tier: US earnings calendar.
    finnhub_api_key: SecretStr | None = None
    # Alpha Vantage free tier: fallback valuation overview (25 requests per day).
    alpha_vantage_api_key: SecretStr | None = None
    eodhd_api_key: SecretStr | None = None
    fmp_api_key: SecretStr | None = None
    twelve_data_api_key: SecretStr | None = None
    un_comtrade_api_key: SecretStr | None = None
    people_data_labs_api_key: SecretStr | None = None
    diffbot_api_key: SecretStr | None = None
    epo_ops_client_id: SecretStr | None = None
    epo_ops_client_secret: SecretStr | None = None
    # EDINET (Japan FSA filings) and Companies House (UK statutory filings): free API keys.
    edinet_api_key: SecretStr | None = None
    companies_house_api_key: SecretStr | None = None
    # OpenDART (Korean disclosure system): free key, used by the news `dart` source.
    dart_api_key: SecretStr | None = None
    # FRED (St. Louis Fed) macro series need a free API key.
    fred_api_key: SecretStr | None = None
    # OpenFIGI works without a key; a key raises its rate limit.
    openfigi_api_key: SecretStr | None = None
    # Stooq daily price downloads need a (free) API key since spring 2026.
    stooq_api_key: SecretStr | None = None

    # Notifications: Telegram Bot API and email through Resend (HTTP API).
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    resend_api_key: SecretStr | None = None
    alert_email_from: str | None = None
    alert_email_to: str | None = None
    # Public URL of the web app, for links in alerts and digests.
    app_url: str | None = None
    # Dead-man's switch: the worker GETs this URL every minute (e.g. a healthchecks.io check),
    # so a stopped or stuck worker raises an alert outside this host.
    heartbeat_ping_url: SecretStr | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _strip_secret_whitespace(cls, value: object, info: ValidationInfo) -> object:
        """A pasted key often carries a trailing newline, which an HTTP header rejects."""
        field = cls.model_fields.get(info.field_name or "")
        if isinstance(value, str) and field is not None and "SecretStr" in str(field.annotation):
            return value.strip()
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("supabase_url")
    @classmethod
    def _project_url_only(cls, value: str | None) -> str | None:
        if not value:
            return value
        value = value.rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path:
            raise ValueError(
                "SUPABASE_URL must be the bare project URL, e.g. https://<ref>.supabase.co "
                "(no /rest/v1 or other path)"
            )
        return value

    def secret_values(self) -> list[str]:
        """Every configured secret, for the log scrubber."""
        secrets = [
            self.database_url,
            self.supabase_jwt_secret,
            self.t212_api_key,
            self.t212_api_secret,
            self.openai_api_key,
            self.google_api_key,
            self.supabase_secret_key,
            self.finnhub_api_key,
            self.alpha_vantage_api_key,
            self.eodhd_api_key,
            self.fmp_api_key,
            self.twelve_data_api_key,
            self.un_comtrade_api_key,
            self.people_data_labs_api_key,
            self.diffbot_api_key,
            self.epo_ops_client_id,
            self.epo_ops_client_secret,
            self.edinet_api_key,
            self.companies_house_api_key,
            self.dart_api_key,
            self.openfigi_api_key,
            self.fred_api_key,
            self.stooq_api_key,
            self.telegram_bot_token,
            self.resend_api_key,
            self.heartbeat_ping_url,
        ]
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
