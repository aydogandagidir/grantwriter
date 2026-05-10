"""Celery wrapper around the saga orchestrator.

The HTTP layer enqueues ``generate_draft_task``; the worker opens a
short-lived asyncpg pool, an optional Redis client (for SSE), and a
shared LLMRouter / CitationVerifier, then drives
:class:`~src.orchestrator.draft_generator.DraftGenerator` to completion.

Pattern mirrors :mod:`src.tasks.exports` — sync Celery wrapper around an
async core (``_run_saga``). Splitting the two lets tests drive the async
core directly without spinning up Celery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.orchestrator.draft_generator import (
    DraftGenerator,
    DraftGeneratorResult,
    build_default_agents,
)
from src.orchestrator.sse_publisher import SSEPublisher
from src.tasks.celery_app import celery_app

if TYPE_CHECKING:
    import asyncpg
    import redis.asyncio as redis_asyncio

    from src.citations import CitationVerifier
    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


async def _run_saga(
    proposal_id: UUID,
    *,
    pool: asyncpg.Pool,
    router: LLMRouter,
    verifier: CitationVerifier,
    redis_client: redis_asyncio.Redis | None,
) -> DraftGeneratorResult:
    """Pure async core — drive one saga run from already-open dependencies.

    The caller owns the pool / router / verifier / redis lifetimes. This
    function acquires three connections from the pool (one for the saga's
    own writes; one each for the parallel Compliance + Distinctiveness
    agents in step 4) and releases them on exit.
    """

    async with (
        pool.acquire() as main_conn,
        pool.acquire() as compliance_conn,
        pool.acquire() as distinctiveness_conn,
    ):
        agents = build_default_agents(
            router=router,
            verifier=verifier,
            compliance_conn=compliance_conn,
            distinctiveness_conn=distinctiveness_conn,
        )
        publisher = SSEPublisher(proposal_id, redis_client=redis_client)
        saga = DraftGenerator(
            proposal_id=proposal_id,
            agents=agents,
            publisher=publisher,
            conn=main_conn,
        )
        return await saga.run()


@celery_app.task(name="orchestrator.generate_draft", bind=True, max_retries=2)  # type: ignore[untyped-decorator]
def generate_draft_task(self: Any, proposal_id: str) -> dict[str, Any]:
    """Celery wrapper. Reads settings, builds dependencies, runs the saga.

    Returns a JSON-safe dump of the saga result so the HTTP layer can
    poll via the result backend. Retries up to 2× on unexpected
    exceptions; saga-level failures (status="failed" or
    "failed_recoverable") are NOT retried — they're returned to the
    user as terminal results so they can fix and re-trigger.
    """

    proposal_uuid = UUID(proposal_id)

    try:
        result = asyncio.run(_run_with_owned_resources(proposal_uuid))
    except Exception as exc:
        logger.exception(
            "generate_draft_task_failed",
            extra={"proposal_id": proposal_id, "retry": self.request.retries},
        )
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc

    return result.model_dump(mode="json")


async def _run_with_owned_resources(proposal_id: UUID) -> DraftGeneratorResult:
    """Open every dependency this task owns, run the saga, close them.

    Kept separate from :func:`_run_saga` so tests can drive the saga
    against test doubles without going through real Postgres / Redis /
    Anthropic. The Celery wrapper exercises this end-to-end against
    real services.
    """

    # Local imports — defer SDK loading until the worker actually runs
    # the task, so unrelated imports of this module stay cheap.
    import redis.asyncio as redis_asyncio

    from src.citations import CitationVerifier
    from src.core.config import get_settings
    from src.core.db import create_pool
    from src.llm.router import build_default_router

    settings = get_settings()

    pool = await create_pool()
    redis_client: redis_asyncio.Redis | None = None
    if settings.redis_url is not None:
        redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url.get_secret_value()
        )

    router = build_default_router(
        db_pool=pool,
        platform_anthropic_key=(
            settings.anthropic_api_key.get_secret_value()
            if settings.anthropic_api_key
            else None
        ),
        platform_openai_key=(
            settings.openai_api_key.get_secret_value()
            if settings.openai_api_key
            else None
        ),
        master_encryption_key=(
            settings.llm_master_encryption_key.get_secret_value()
            if settings.llm_master_encryption_key
            else None
        ),
    )
    verifier = CitationVerifier()

    try:
        return await _run_saga(
            proposal_id,
            pool=pool,
            router=router,
            verifier=verifier,
            redis_client=redis_client,
        )
    finally:
        await verifier.aclose()
        if redis_client is not None:
            await redis_client.aclose()
        await pool.close()


__all__ = [
    "_run_saga",
    "generate_draft_task",
]
