"""FastAPI application entrypoint.

Use the ``create_app`` factory for tests and overrides; the module-level
``app`` exists so production runs as ``uvicorn src.main:app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes.citations import router as citations_router
from src.api.routes.jobs import router as jobs_router
from src.api.routes.proposals import router as proposals_router
from src.core.config import SettingsDep, get_settings
from src.core.db import create_pool
from src.core.logging import configure_logging

_PACKAGE_NAME = "bluedev-grantwriter-api"

logger = logging.getLogger(__name__)


def _resolve_version(fallback: str) -> str:
    """Return the installed package version, falling back to settings.

    ``importlib.metadata.version`` is the source of truth in production
    (no drift from ``pyproject.toml``). In source-mode tests the package
    may not be installed yet, so we degrade to the configured fallback.
    """

    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return fallback


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the asyncpg pool when ``DATABASE_URL`` is configured.

    When unset (test mode, smoke deploys), ``app.state.db_pool`` is ``None``
    and any DB-bound endpoint returns 503 via ``get_db``.
    """

    settings = get_settings()
    if settings.database_url is not None:
        app.state.db_pool = await create_pool()
        logger.info("db_pool_opened")
    else:
        app.state.db_pool = None
        logger.warning("db_pool_skipped: DATABASE_URL not set")
    try:
        yield
    finally:
        if app.state.db_pool is not None:
            await app.state.db_pool.close()
            logger.info("db_pool_closed")
        # Redis client is opened lazily on first SSE request — close it
        # here if any handler created one.
        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is not None:
            await redis_client.aclose()
            logger.info("redis_client_closed")


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""

    settings = get_settings()
    configure_logging(settings.log_level)

    app_version = _resolve_version(settings.app_version)

    app = FastAPI(
        title="Bluedev GrantWriter API",
        version=app_version,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={
                "event": "unhandled_exception",
                "path": request.url.path,
                "method": request.method,
                "exc_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.get("/health", tags=["meta"])
    async def health(settings: SettingsDep) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": _resolve_version(settings.app_version),
        }

    app.include_router(proposals_router)
    app.include_router(citations_router)
    app.include_router(jobs_router)

    return app


app = create_app()
