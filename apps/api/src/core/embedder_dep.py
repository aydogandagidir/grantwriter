"""Embedder as a FastAPI dependency.

Mirrors :func:`src.core.llm_dep.get_llm_router` — the embedder is built
once per app process and cached on ``app.state``. Production wires
:class:`~src.rag.embedder.OpenAIEmbedder` from the OpenAI key in
settings; tests override via
``app.dependency_overrides[get_embedder]`` with
:class:`~src.rag.embedder.DeterministicEmbedder`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

from src.core.config import get_settings

if TYPE_CHECKING:
    from src.rag.base import Embedder


async def get_embedder(request: Request) -> Embedder:
    """Return a process-cached :class:`~src.rag.base.Embedder`.

    First call builds and caches; subsequent calls return the cached
    instance. Returns 503 when no OpenAI key is configured — the
    matcher / idea-creation paths need real embeddings, and silently
    returning zero vectors would corrupt the cosine layer.
    """

    cached = getattr(request.app.state, "embedder", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    from src.rag.embedder import OpenAIEmbedder

    settings = get_settings()
    openai_key = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key
        else None
    )
    if openai_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No embedding provider configured (OPENAI_API_KEY unset)",
        )

    embedder = OpenAIEmbedder(api_key=openai_key)
    request.app.state.embedder = embedder
    return embedder


__all__ = ["get_embedder"]
