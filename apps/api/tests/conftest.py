"""Shared pytest fixtures for the API test suite.

- The base fixtures (`app`, `client`) come from the dreamy-beaver foundation —
  they construct a fresh app per test via `create_app()` and an httpx
  AsyncClient over ASGITransport.
- The distinctiveness-specific fixtures (`deterministic_embedder`,
  `live_db_pool`) opt-in only the tests that need them.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.config import get_settings
from src.main import create_app

# asyncpg's IO transport does not support Windows' default ProactorEventLoop
# (it expects SelectorEventLoop semantics). pytest-asyncio honours the policy
# set at conftest import time, so switch here before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def deterministic_embedder(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch the embeddings module so tests don't hit OpenAI.

    Returns a vector derived from sha256(text) mapped onto the unit sphere.
    Two identical texts always return the same vector; two distinct texts
    return roughly orthogonal vectors. Useful for asserting scorer behavior
    without controlling exact similarities.
    """
    from src.llm import embeddings as embeddings_module

    async def _embed(text: str, *, model: str | None = None) -> list[float]:
        return _hash_to_unit_vector(text, dim=embeddings_module.get_settings().embedding_dim)

    async def _embed_batch(
        texts: Sequence[str], **_kwargs: Any
    ) -> list[list[float]]:
        dim = embeddings_module.get_settings().embedding_dim
        return [_hash_to_unit_vector(t, dim=dim) for t in texts]

    monkeypatch.setattr(embeddings_module, "embed", _embed)
    monkeypatch.setattr(embeddings_module, "embed_batch", _embed_batch)

    # Also patch the imported reference inside compliance.distinctiveness.
    from src.compliance import distinctiveness as dist_module

    monkeypatch.setattr(dist_module, "embed", _embed)

    mock = AsyncMock()
    mock.embed = _embed
    return mock


def _hash_to_unit_vector(text: str, dim: int = 3072) -> list[float]:
    """Cheap deterministic 'embedding': hash → repeated bytes → unit-normalize."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    raw = (h * ((dim // len(h)) + 1))[:dim]
    floats = [(b - 128) / 128.0 for b in raw]
    norm = math.sqrt(sum(v * v for v in floats)) or 1.0
    return [v / norm for v in floats]


# ---------------------------------------------------------------------------
# Live DB (integration only)
# ---------------------------------------------------------------------------


def _supabase_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent / "infra" / "supabase"


@pytest.fixture
async def live_db_pool() -> AsyncIterator[Any]:
    """Connect to the integration test Postgres (skip if not configured).

    **TEST_DATABASE_URL is required** — TICKET-002 closed the previous
    default (``...localhost:5432/bluedev``) because integration suites
    were trampling each other's schema on developer laptops where the
    generic ``bluedev`` database had drifted. Each developer is expected
    to point TEST_DATABASE_URL at a dedicated test DB (CI uses
    ``bluedev_test`` via the api-ci workflow).

    The fixture applies the Supabase auth stub + every migration in
    lexical order before yielding the pool. Migrations are idempotent
    (``CREATE TABLE IF NOT EXISTS``) so re-applying them per fixture
    call against a clean test DB is safe.
    """
    import asyncpg

    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping live-DB integration "
            "tests. Point it at a dedicated test database (e.g. "
            "postgresql://postgres:postgres@localhost:5432/bluedev_test) "
            "to run this suite locally."
        )
    try:
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        if pool is None:
            raise RuntimeError("create_pool returned None")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"live Postgres not reachable at TEST_DATABASE_URL: {exc}")

    supabase_dir = _supabase_dir()
    auth_stub = supabase_dir / "auth_stub.sql"
    migrations_dir = supabase_dir / "migrations"

    try:
        async with pool.acquire() as conn:
            if auth_stub.exists():
                await conn.execute(auth_stub.read_text(encoding="utf-8"))
            for migration in sorted(migrations_dir.glob("*.sql")):
                await conn.execute(migration.read_text(encoding="utf-8"))
        yield pool
    finally:
        await pool.close()
