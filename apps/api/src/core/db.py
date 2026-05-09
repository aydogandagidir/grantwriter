"""Async Postgres pool with pgvector codecs registered.

The pool is created at app startup (see `src/main.py` lifespan) and stored on
`app.state.db_pool`. Route handlers acquire connections via `Depends(get_db)`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import asyncpg
from pgvector.asyncpg import register_vector

from src.core.config import get_settings

if TYPE_CHECKING:
    from fastapi import Request


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register pgvector codecs on each new pooled connection.

    `register_vector` from pgvector.asyncpg installs both `vector` and `halfvec`
    type adapters in recent versions (>=0.3.0). On older releases only `vector`
    is registered; the script and scorer will still work because they cast to
    halfvec at the SQL boundary.
    """
    await register_vector(conn)


async def create_pool() -> asyncpg.Pool:
    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=_init_connection,
    )
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    return pool


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency yielding a connection from the app pool."""
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn
