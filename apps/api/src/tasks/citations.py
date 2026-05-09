"""Batch citation verification — Celery task + async core.

The orchestrator (S2.D9+) enqueues this from inside
``generate_draft_task`` after the writer chain finishes; for v1
nothing calls it yet. Tests exercise :func:`_run_batch_verify`
directly with a stub :class:`CitationVerifier`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import redis.asyncio as redis_async

from src.agents.hallucination_hunter import HuntReport, hunt_report_from_results
from src.citations import (
    Citation,
    CitationCache,
    CitationVerifier,
    InMemoryCacheBackend,
    RedisCacheBackend,
    extract_citations,
)
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


_SECTION_KEYS = (
    ("excellence_md", "excellence"),
    ("impact_md", "impact"),
    ("implementation_md", "implementation"),
)


async def _run_batch_verify(
    proposal: dict[str, Any],
    *,
    verifier: CitationVerifier,
) -> dict[str, Any]:
    """Pure async core. Returns the :class:`HuntReport` as a dict."""

    draft = proposal.get("draft") or {}
    citations: list[Citation] = []
    section_for_index: list[str] = []
    for key, label in _SECTION_KEYS:
        body = str(draft.get(key) or "")
        if not body.strip():
            continue
        for citation in extract_citations(body):
            citations.append(citation)
            section_for_index.append(label)

    if not citations:
        report = HuntReport(
            total_citations=0,
            verified=0,
            partial_match=0,
            fabricated=0,
            not_found=0,
            errors=0,
            verification_rate=1.0,
            recommendation="ok",
        )
        return report.model_dump()

    results = await verifier.verify_many(citations)
    report = hunt_report_from_results(citations, results, section_for_index=section_for_index)
    logger.info(
        "batch_verify_proposal_done",
        extra={
            "proposal_id": str(proposal.get("id") or ""),
            "total_citations": report.total_citations,
            "fabricated": report.fabricated,
            "not_found": report.not_found,
            "recommendation": report.recommendation,
        },
    )
    return report.model_dump()


@celery_app.task(name="citations.batch_verify_proposal", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def batch_verify_proposal_task(self: Any, proposal: dict[str, Any]) -> dict[str, Any]:
    """Celery wrapper. Builds production HTTP client + cache, runs the
    async core, returns the JSON-safe HuntReport dict."""

    from src.core.config import get_settings

    settings = get_settings()

    async def _go() -> dict[str, Any]:
        cache: CitationCache
        redis_client: redis_async.Redis | None = None
        if settings.redis_url is not None:
            redis_client = redis_async.from_url(  # type: ignore[no-untyped-call]
                settings.redis_url.get_secret_value(),
                encoding="utf-8",
                decode_responses=False,
            )
            cache = CitationCache(backend=RedisCacheBackend(redis_client))
        else:
            cache = CitationCache(backend=InMemoryCacheBackend())

        async with httpx.AsyncClient(timeout=15.0) as client:
            verifier = CitationVerifier(client=client, cache=cache)
            try:
                return await _run_batch_verify(proposal, verifier=verifier)
            finally:
                if redis_client is not None:
                    await redis_client.close()

    try:
        return asyncio.run(_go())
    except Exception as exc:
        logger.exception("batch_verify_task_failed", extra={"proposal_id": proposal.get("id")})
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


__all__ = ["_run_batch_verify", "batch_verify_proposal_task"]
