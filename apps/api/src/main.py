"""FastAPI application entrypoint.

Use the ``create_app`` factory for tests and overrides; the module-level
``app`` exists so production runs as ``uvicorn src.main:app``.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes.citations import router as citations_router
from src.api.routes.proposals import router as proposals_router
from src.core.config import SettingsDep, get_settings
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

    return app


app = create_app()
