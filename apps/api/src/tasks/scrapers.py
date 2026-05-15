"""Celery wrappers around the scraper runner.

The HTTP layer / Celery beat enqueues :func:`run_scraper_task`; the worker
opens a short-lived asyncpg pool and drives :class:`ScraperRunner` to
completion.

Pattern mirrors :mod:`src.tasks.orchestrator` — sync Celery wrapper
around an async core (:func:`_run_scrape`). Splitting the two lets the
tests drive the async core directly without spinning up Celery.

Beat schedule is configured in :mod:`src.tasks.celery_app`. The frequency
table (per docs/programs/README.md):

    eu_ft_portal    daily 03:00 Europe/Istanbul
    nlnet           weekly Monday 04:00 (deterministic, infrequent)
    tubitak         weekly Wednesday 05:00 (TR-business-hours friendly)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from src.core.config import get_settings
from src.core.db import create_pool
from src.scrapers.base import NormalizedCall, ScraperRunResult
from src.scrapers.runner import ScraperRunner, TriggeredBy
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _enqueue_guideline_ingest(call_id: UUID, call: NormalizedCall, _inserted: bool) -> None:
    """``on_persist`` callback — schedules guideline ingest when a PDF
    URL is present.

    Fires on both inserts and updates. The ingestor short-circuits on
    unchanged (call_id, file_hash), so the cost of re-firing is one
    HTTP HEAD-equivalent + sha256.
    """

    if not (call.call_pdf_url or call.work_programme_pdf_url):
        return
    # Lazy import — keeps src.tasks.scrapers light enough to import in
    # unit tests that don't touch the ingest path.
    from src.tasks.guidelines import ingest_call_guideline_task

    try:
        ingest_call_guideline_task.delay(str(call_id))
    except Exception:
        logger.exception(
            "guideline_ingest_dispatch_failed",
            extra={"call_id": str(call_id), "external_id": call.external_id},
        )


async def _run_scrape(
    source: str,
    *,
    triggered_by: TriggeredBy = "beat",
) -> dict[str, Any]:
    """Pure async core — open pool, run scraper, return summary dict.

    Returns the run summary as a plain dict so Celery's JSON serialiser
    can round-trip it back to the caller. The serialisation also covers
    the case where the caller wants to surface progress via flower/UI.
    """

    pool = await create_pool()
    try:
        runner = ScraperRunner(pool=pool, on_persist=_enqueue_guideline_ingest)
        # The Literal type guards source values; we cast through Any
        # because Celery sees a plain string.
        result: ScraperRunResult = await runner.run(
            source=source,  # type: ignore[arg-type]
            triggered_by=triggered_by,
        )
    finally:
        await pool.close()
    return _summarise(result)


def _summarise(result: ScraperRunResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "duration_seconds": round(result.duration_seconds, 3),
        "calls_discovered": result.calls_discovered,
        "calls_persisted": result.calls_persisted,
        "calls_updated": result.calls_updated,
        "calls_failed": result.calls_failed,
        "error_count": len(result.errors),
    }


# Celery's @task has no py.typed marker → ``[untyped-decorator]`` flag
# is unavoidable under mypy strict; suppress at the decorator line.
@celery_app.task(name="src.tasks.scrapers.run_scraper_task", bind=True)  # type: ignore[untyped-decorator]
def run_scraper_task(_self: Any, source: str, *, triggered_by: str = "beat") -> dict[str, Any]:
    """Celery task: run one scraper to completion.

    Errors at the orchestration level (e.g. discover() itself raising)
    propagate so Celery can retry. Per-call failures are contained in
    the runner and only surface via the ``error_count`` field.
    """

    settings = get_settings()
    if settings.database_url is None:
        # Don't crash beat with retries when the worker is misconfigured —
        # log and acknowledge so the operator notices the alert without
        # a retry storm.
        logger.error(
            "scraper_task_misconfigured_no_database_url",
            extra={"source": source},
        )
        return {
            "source": source,
            "status": "skipped",
            "reason": "DATABASE_URL not configured",
        }

    return asyncio.run(_run_scrape(source, triggered_by=triggered_by))  # type: ignore[arg-type]


__all__ = ["run_scraper_task"]
