"""Test fixtures.

Most tests in this suite are unit tests with mocked DB and mocked OpenAI.
The few `@pytest.mark.integration` tests need a live Postgres+pgvector — they
opt-in via the `live_db_pool` fixture and are skipped when the DB isn't
reachable (so `pytest` works on a laptop with no docker).
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from src.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def _migrations_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent / "infra" / "supabase" / "migrations"


@pytest.fixture
async def live_db_pool() -> AsyncIterator[Any]:
    """Connect to a local Postgres (skip the test if unreachable)."""
    import asyncpg

    dsn = os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/bluedev"
        ),
    )
    try:
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        if pool is None:
            raise RuntimeError("create_pool returned None")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"live Postgres not reachable: {exc}")

    try:
        async with pool.acquire() as conn:
            for migration in sorted(_migrations_dir().glob("*.sql")):
                await conn.execute(migration.read_text(encoding="utf-8"))
        yield pool
    finally:
        await pool.close()
