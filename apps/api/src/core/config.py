"""Application settings.

All configuration loaded from environment variables. See `.env.example` (when
added) for the canonical list. Pydantic Settings validates types at startup so
misconfiguration fails loud rather than at first request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/postgres",
        description="asyncpg-compatible Postgres DSN. Must point at a database with pgvector installed.",
    )
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    openai_api_key: str = Field(default="", description="OpenAI API key for embeddings.")
    anthropic_api_key: str = Field(default="", description="Anthropic API key for chat completions.")

    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072

    supabase_jwt_secret: str = Field(
        default="dev-secret-change-me",
        description="HS256 secret used to verify Supabase-issued JWTs.",
    )
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_algorithm: str = "HS256"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
