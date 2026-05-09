"""FastAPI app entry point.

Lifespan opens the asyncpg pool, exposes it on `app.state.db_pool`, and
closes it on shutdown. The `/health` endpoint is intentionally unauthenticated
(it's the readiness probe target).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from src.api.v1.router import router as v1_router
from src.core.db import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.db_pool = await create_pool()
    try:
        yield
    finally:
        await app.state.db_pool.close()


app = FastAPI(
    title="Bluedev GrantWriter API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(v1_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": app.version}
