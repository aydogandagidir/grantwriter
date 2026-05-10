"""LLM router as a FastAPI dependency.

The router is built once per app process and cached on ``app.state``,
mirroring the ``db_pool`` lifecycle. Tests override via
``app.dependency_overrides[get_llm_router]``; production wiring uses
:func:`build_default_router` from settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

from src.core.config import get_settings

if TYPE_CHECKING:
    from src.llm.router import LLMRouter


async def get_llm_router(request: Request) -> LLMRouter:
    """Return a process-cached :class:`LLMRouter`.

    First call builds and caches; subsequent calls return the cached
    instance. Returns 503 if no LLM provider key is configured (neither
    BYOK in DB nor platform key in env).
    """

    from src.llm.router import build_default_router

    cached = getattr(request.app.state, "llm_router", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    settings = get_settings()
    pool = getattr(request.app.state, "db_pool", None)

    platform_anthropic = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key
        else None
    )
    platform_openai = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key
        else None
    )
    master = (
        settings.llm_master_encryption_key.get_secret_value()
        if settings.llm_master_encryption_key
        else None
    )

    if platform_anthropic is None and platform_openai is None and pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured",
        )

    router = build_default_router(
        db_pool=pool,
        platform_anthropic_key=platform_anthropic,
        platform_openai_key=platform_openai,
        master_encryption_key=master,
    )
    request.app.state.llm_router = router
    return router


__all__ = ["get_llm_router"]
