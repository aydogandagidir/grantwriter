"""Application configuration via Pydantic Settings v2.

Settings are read from environment variables and an optional ``.env`` file.
Required vars have sane defaults so the skeleton boots with an empty ``.env``;
feature PRs (DB, LLM, etc.) flip placeholder secrets to required as they land.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
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

    cors_origins: list[str] = Field(default_factory=list)

    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance.

    Lazy + cached so tests can override via ``app.dependency_overrides``
    and so import-time side effects stay minimal.
    """

    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
"""FastAPI dependency alias — inject as ``settings: SettingsDep``."""
