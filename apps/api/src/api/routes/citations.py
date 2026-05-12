"""Citation-scoped HTTP endpoints.

S2.D7.T1 ships the synchronous verify endpoint used by the editor for
on-demand re-checks. The DB-load path lands when the proposal CRUD
service does (S2.D8+); for now the endpoint accepts the citation in
the request body. The ``{citation_id}`` is the stable handle the
frontend uses for optimistic UI / polling.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

import httpx
import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from src.citations import (
    Citation,
    CitationCache,
    CitationVerifier,
    InMemoryCacheBackend,
    RedisCacheBackend,
    VerificationResult,
)
from src.core.auth import CurrentUserId
from src.core.config import Settings, SettingsDep
from src.core.rate_limit import (
    CITATION_VERIFY,
    RateLimitDecision,
    rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/citations", tags=["citations"])


class VerifyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: UUID
    result: VerificationResult


@router.post(
    "/{citation_id}/verify",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify a single citation against Crossref / OpenAlex",
)
async def verify_citation(
    citation_id: UUID,
    body: Citation,
    user_id: CurrentUserId,
    settings: SettingsDep,
    _rate_check: Annotated[
        RateLimitDecision, Depends(rate_limit(CITATION_VERIFY))
    ],
) -> VerifyResponse:
    """Run the 3-stage cascade and return the result.

    The endpoint is synchronous from the user's perspective — the editor
    asks for a single citation re-check and gets the answer back in
    one round-trip. Batch verification (full proposal) goes through the
    Celery task in :mod:`src.tasks.citations`.

    Rate-limited per docs/09 §8 (50 / 60s per user) — looser than LLM
    calls because Crossref/OpenAlex are cheap and cached.
    """

    cache = await build_citation_cache(settings)

    async with httpx.AsyncClient(timeout=10.0) as client:
        verifier = CitationVerifier(client=client, cache=cache)
        result = await verifier.verify(body)

    logger.info(
        "citation_verified",
        extra={
            "citation_id": str(citation_id),
            "user_id": str(user_id),
            "status": result.status,
            "source": result.source,
        },
    )
    return VerifyResponse(citation_id=citation_id, result=result)


async def build_citation_cache(settings: Settings) -> CitationCache:
    """Wire Redis when ``REDIS_URL`` is set; fall back to in-memory.

    Public helper — reused by ``apps/api/src/api/routes/proposals.py``'s
    ``validate_proposal`` endpoint when it runs HallucinationHunter
    alongside ComplianceReviewer (S3.D13.T1 completion).
    """

    if settings.redis_url is None:
        logger.debug("citation_cache_using_in_memory_backend")
        return CitationCache(backend=InMemoryCacheBackend())

    client = redis_async.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url.get_secret_value(),
        encoding="utf-8",
        decode_responses=False,
    )
    return CitationCache(backend=RedisCacheBackend(client))


__all__ = ["VerifyResponse", "build_citation_cache", "router"]
