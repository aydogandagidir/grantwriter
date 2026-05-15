"""Celery wrappers for the funder-guideline ingest pipeline.

The scraper runner enqueues :func:`ingest_call_guideline_task` whenever
it upserts a call that carries a ``call_pdf_url`` (or
``work_programme_pdf_url``). The worker:

  1. Looks up the call row.
  2. Picks the best available PDF url
     (``work_programme_pdf_url`` first — that's the substantive doc
     funders publish; ``call_pdf_url`` is often a stub topic-page
     export).
  3. Builds a :class:`~src.rag.guideline_ingestor.GuidelineIngestor`
     with the production embedder and an asyncpg pool, then runs the
     ingest.

Idempotency: the ingestor short-circuits on unchanged (call_id,
file_hash), so it's safe to re-fire this task on every scrape run.

Like the other Celery tasks in this package, the wrapper pattern is
``sync Celery task → async core`` so the unit tests can exercise the
core via :func:`asyncio.run` without spinning up Celery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import asyncpg

from src.core.config import get_settings
from src.core.db import create_pool
from src.rag.base import Embedder
from src.rag.embedder import OpenAIEmbedder
from src.rag.guideline_ingestor import (
    GuidelineIngestError,
    GuidelineIngestor,
    IngestResult,
)
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _build_embedder() -> Embedder | None:
    """Build the production embedder from settings, or None if unkeyed.

    Tests inject a :class:`DeterministicEmbedder` via the ``embedder``
    argument on :func:`_run_ingest`; production goes through this
    factory when the parameter is omitted.
    """

    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return OpenAIEmbedder(api_key=settings.openai_api_key.get_secret_value())


async def _run_ingest(
    *,
    call_id: UUID,
    pool: asyncpg.Pool | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """Pure async core — look up the call, dispatch to the ingestor.

    ``pool`` and ``embedder`` are dependency-injected; production calls
    with neither, tests pass both.
    """

    owns_pool = False
    if pool is None:
        pool = await create_pool()
        owns_pool = True

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  id,
                  programme_id,
                  call_pdf_url,
                  work_programme_pdf_url,
                  language,
                  title
                FROM calls
                WHERE id = $1
                """,
                call_id,
            )
        if row is None:
            return {
                "status": "skipped",
                "call_id": str(call_id),
                "reason": "call_not_found",
            }

        source_url = row["work_programme_pdf_url"] or row["call_pdf_url"]
        if not source_url:
            return {
                "status": "skipped",
                "call_id": str(call_id),
                "reason": "no_pdf_url",
            }

        active_embedder = embedder if embedder is not None else _build_embedder()
        if active_embedder is None:
            return {
                "status": "skipped",
                "call_id": str(call_id),
                "reason": "no_embedder_configured",
            }
        ingestor = GuidelineIngestor(pool=pool, embedder=active_embedder)
        try:
            result: IngestResult = await ingestor.ingest(
                source_url=source_url,
                programme_id=row["programme_id"],
                call_id=call_id,
                document_type=(
                    "work_programme" if row["work_programme_pdf_url"] else "call_guideline"
                ),
                title=row["title"],
                language=row["language"],
            )
        except GuidelineIngestError as exc:
            logger.warning(
                "guideline_ingest_failed",
                extra={
                    "call_id": str(call_id),
                    "source_url": source_url,
                    "error": str(exc),
                },
            )
            return {
                "status": "failed",
                "call_id": str(call_id),
                "reason": str(exc),
            }
    finally:
        if owns_pool:
            await pool.close()

    return {
        "status": "ok",
        "call_id": str(call_id),
        "guideline_id": str(result.guideline_id),
        "from_cache": result.from_cache,
        "chunk_count": result.chunk_count,
        "page_count": result.page_count,
        "section_count": result.section_count,
        "file_hash": result.file_hash,
    }


# Celery's @task has no py.typed marker → ``[untyped-decorator]`` flag
# is unavoidable under mypy strict; suppress at the decorator line.
@celery_app.task(  # type: ignore[untyped-decorator]
    name="src.tasks.guidelines.ingest_call_guideline_task",
    bind=True,
    autoretry_for=(GuidelineIngestError,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
)
def ingest_call_guideline_task(_self: Any, call_id: str) -> dict[str, Any]:
    """Celery task: ingest the funder guideline PDF for one call.

    On configuration miss (no DATABASE_URL) the task returns a skipped-
    status dict rather than retrying — keeps beat from drowning in
    retries when the worker is mis-deployed.
    """

    settings = get_settings()
    if settings.database_url is None:
        logger.error(
            "guideline_task_misconfigured_no_database_url",
            extra={"call_id": call_id},
        )
        return {
            "status": "skipped",
            "call_id": call_id,
            "reason": "DATABASE_URL not configured",
        }
    return asyncio.run(_run_ingest(call_id=UUID(call_id)))


__all__ = ["ingest_call_guideline_task"]
