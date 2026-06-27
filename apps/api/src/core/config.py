"""Application configuration via Pydantic Settings v2.

Settings are read from environment variables and an optional ``.env`` file.
Required vars have sane defaults so the skeleton boots with an empty ``.env``;
feature PRs (DB, LLM, etc.) flip placeholder secrets to required as they land.

Sensitive values use :class:`pydantic.SecretStr` so they don't appear in logs
or repr output. Callers must call ``.get_secret_value()`` to obtain the raw
string.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["development", "staging", "production", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration sourced from env + ``.env``.

    See ``apps/api/.env.example`` for the documented surface.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "development"
    log_level: LogLevel = "INFO"
    app_version: str = "0.1.0"

    # NoDecode disables pydantic-settings' built-in JSON decoding for this
    # field so the validator below sees the raw env string. Without it, a
    # value like ``https://a.com,https://b.com`` (no JSON brackets) raises
    # at boot, and a slightly malformed JSON array silently yields []. CORS
    # config is too load-bearing to be that brittle.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> list[str]:
        """Accept a JSON array, a comma-separated string, or a real list.

        - ``["https://a.com","https://b.com"]`` (JSON array)
        - ``https://a.com,https://b.com`` (comma-separated, no quotes)
        - ``https://a.com`` (single origin)
        - ``""`` / ``None`` → ``[]``
        """

        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            return [item.strip() for item in text.split(",") if item.strip()]
        return []

    # Database / Redis
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # LLM provider keys (BYOK or platform-managed via key_vault)
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_master_encryption_key: SecretStr | None = None

    # Embeddings (used by RAG + distinctiveness scorer)
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072

    # Supabase
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_bucket: str = "exports"

    # Supabase JWT auth
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_algorithm: str = "HS256"

    # Celery (defaults to redis_url when unset)
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # Iyzico (TR payments). Outbound creds (api_key/secret_key) are
    # required for the checkout/cancel endpoints to function; the
    # webhook_secret is independent (used only on the inbound side).
    iyzico_api_key: SecretStr | None = None
    iyzico_secret_key: SecretStr | None = None
    iyzico_base_url: str = "https://sandbox-api.iyzipay.com"
    iyzico_webhook_secret: SecretStr | None = None
    # Where the FE renders the post-checkout return page. The hosted
    # checkout redirects the user here after they enter card details.
    iyzico_callback_url: str = "https://app.bluedev.dev/billing/return"

    # Observability — both packages are optional; init noop when DSN/token
    # is missing OR the SDK isn't installed (see src/core/observability.py).
    sentry_dsn: SecretStr | None = None
    sentry_environment: str | None = None  # defaults to app_env at init
    sentry_traces_sample_rate: float = 0.0  # errors only by default
    sentry_release: str | None = None  # set to git SHA at deploy time (TICKET-003)
    logtail_token: SecretStr | None = None
    observability_enabled: bool = True

    # Email (Resend) — optional; init noop when key absent OR package missing.
    # Mirrors the observability pattern in src/core/observability.py.
    resend_api_key: SecretStr | None = None
    email_from: str = "Bluedev GrantWriter <noreply@bluedev.dev>"
    app_url: str = "https://app.bluedev.dev"  # used to compose invite accept URLs
    email_enabled: bool = True  # kill-switch independent of API key presence
    # Resend uses Svix-style webhook signatures; set this to the secret
    # the operator copied from the Resend dashboard. Required to verify
    # POST /api/v1/notifications/resend-webhook payloads.
    resend_webhook_secret: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance.

    Lazy + cached so tests can override via ``app.dependency_overrides``
    and so import-time side effects stay minimal.
    """

    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
"""FastAPI dependency alias — inject as ``settings: SettingsDep``."""
