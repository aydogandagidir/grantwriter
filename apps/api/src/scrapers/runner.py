"""ScraperRunner — orchestrates one scraper run end-to-end.

Pipeline per source:

    1. Open one scraper instance (from :data:`SCRAPER_REGISTRY`).
    2. Insert a ``scraper_runs`` row with status=started.
    3. ``async for raw in scraper.discover()``
         → ``scraper.fetch_call_detail(external_id, discover_payload=raw)``
         → ``scraper.normalize(detail)`` → ``NormalizedCall``
         → ``self.persist(conn, call)`` (upsert by ``(source, external_id)``)
    4. Tally outcomes (new vs updated vs failed) and finalize the
       ``scraper_runs`` row.

A failure on one call is contained: the runner logs the error, records
it in ``scraper_runs.errors`` jsonb, and continues with the next call.
A scraper-level fatal (the call to ``discover()`` itself raising) bubbles
out so Celery beat can retry the task.

The runner does not embed call vectors or ingest guideline PDFs — those
live in separate Celery tasks (Faz 3) so a slow ingest can't block a
fast scrape.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from src.scrapers import SCRAPER_REGISTRY
from src.scrapers.base import (
    BaseScraper,
    CallSource,
    NormalizedCall,
    ScraperRunResult,
)
from src.scrapers.normalization import compute_lifecycle_status

logger = logging.getLogger(__name__)


TriggeredBy = Literal["beat", "manual", "test"]


class ScraperRunner:
    """Run one scraper end-to-end against a live database pool."""

    def __init__(self, *, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def run(
        self,
        source: CallSource,
        *,
        scraper: BaseScraper | None = None,
        triggered_by: TriggeredBy = "beat",
        triggered_by_user_id: UUID | None = None,
    ) -> ScraperRunResult:
        """Discover, normalize, and persist every call from ``source``.

        ``scraper`` is optional — when not provided the registry's class
        is instantiated with no arguments. Pass a pre-built scraper to
        inject custom config (test fixtures, mock HTTP clients).
        """

        if scraper is None:
            scraper_cls = SCRAPER_REGISTRY.get(source)
            if scraper_cls is None:
                raise KeyError(f"No scraper registered for source={source!r}")
            scraper = scraper_cls()

        started_at = datetime.now(UTC)
        run_id = await self._insert_run_started(
            source=source,
            started_at=started_at,
            triggered_by=triggered_by,
            triggered_by_user_id=triggered_by_user_id,
        )

        discovered = persisted = updated = failed = 0
        errors: list[dict[str, Any]] = []

        try:
            async for raw in scraper.discover():
                discovered += 1
                external_id = str(raw.get("external_id") or "?")
                try:
                    detail = await scraper.fetch_call_detail(
                        external_id, discover_payload=raw
                    )
                    call = await scraper.normalize(detail)
                    async with self._pool.acquire() as conn:
                        was_new = await self.persist(conn, call)
                    if was_new:
                        persisted += 1
                    else:
                        updated += 1
                except Exception as exc:
                    failed += 1
                    errors.append(
                        {
                            "external_id": external_id,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        }
                    )
                    logger.exception(
                        "scraper_call_failed",
                        extra={
                            "source": source,
                            "external_id": external_id,
                        },
                    )
        finally:
            close = getattr(scraper, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    logger.exception("scraper_aclose_failed")

        finished_at = datetime.now(UTC)
        result = ScraperRunResult(
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            calls_discovered=discovered,
            calls_persisted=persisted,
            calls_updated=updated,
            calls_failed=failed,
            errors=errors,
        )
        await self._update_run_finished(run_id, result)
        logger.info(
            "scraper_run_completed",
            extra={
                "source": source,
                "duration_seconds": result.duration_seconds,
                "discovered": discovered,
                "persisted": persisted,
                "updated": updated,
                "failed": failed,
            },
        )
        return result

    # ── persist (upsert) ─────────────────────────────────────────────

    async def persist(
        self, conn: asyncpg.Connection, call: NormalizedCall
    ) -> bool:
        """Upsert one normalized call. Returns ``True`` when a brand-new
        row was inserted, ``False`` when an existing one was updated.

        Detection uses ``xmax = 0`` (Postgres trick): xmax is the
        transaction id that deleted/locked the row — it's ``0`` on a
        fresh INSERT and non-zero on an UPDATE path through ON CONFLICT.
        Avoids needing a separate SELECT before write.
        """

        lifecycle = compute_lifecycle_status(call.deadline)

        row = await conn.fetchrow(
            """
            INSERT INTO calls (
              programme_id, source, external_id, title, language,
              call_text, call_url, call_pdf_url, deadline, opening_at,
              budget_total_eur, budget_per_project_min_eur,
              budget_per_project_max_eur, trl_min, trl_max,
              topic_keywords, sectors, geo_scope, eligibility_tags,
              eligibility_summary, raw_metadata, status, scope_summary,
              funding_rate_pct, application_form_url,
              work_programme_pdf_url, partner_consortium_required,
              source_url_canonical, agency_id, last_seen_at
            ) VALUES (
              $1, $2, $3, $4, $5,
              $6, $7, $8, $9, $10,
              $11, $12, $13, $14, $15,
              $16::text[], $17::text[], $18::text[], $19::text[],
              $20::jsonb, $21::jsonb, $22, $23,
              $24, $25, $26, $27, $28, $29, now()
            )
            ON CONFLICT (source, external_id) DO UPDATE SET
              programme_id = EXCLUDED.programme_id,
              title = EXCLUDED.title,
              language = EXCLUDED.language,
              call_text = EXCLUDED.call_text,
              call_url = EXCLUDED.call_url,
              call_pdf_url = EXCLUDED.call_pdf_url,
              deadline = EXCLUDED.deadline,
              opening_at = EXCLUDED.opening_at,
              budget_total_eur = EXCLUDED.budget_total_eur,
              budget_per_project_min_eur = EXCLUDED.budget_per_project_min_eur,
              budget_per_project_max_eur = EXCLUDED.budget_per_project_max_eur,
              trl_min = EXCLUDED.trl_min,
              trl_max = EXCLUDED.trl_max,
              topic_keywords = EXCLUDED.topic_keywords,
              sectors = EXCLUDED.sectors,
              geo_scope = EXCLUDED.geo_scope,
              eligibility_tags = EXCLUDED.eligibility_tags,
              eligibility_summary = EXCLUDED.eligibility_summary,
              raw_metadata = EXCLUDED.raw_metadata,
              status = EXCLUDED.status,
              scope_summary = EXCLUDED.scope_summary,
              funding_rate_pct = EXCLUDED.funding_rate_pct,
              application_form_url = EXCLUDED.application_form_url,
              work_programme_pdf_url = EXCLUDED.work_programme_pdf_url,
              partner_consortium_required = EXCLUDED.partner_consortium_required,
              source_url_canonical = EXCLUDED.source_url_canonical,
              agency_id = EXCLUDED.agency_id,
              last_seen_at = now()
            RETURNING (xmax = 0) AS inserted
            """,
            call.programme_id,
            call.source,
            call.external_id,
            call.title,
            call.language,
            call.call_text,
            call.call_url,
            call.call_pdf_url,
            call.deadline,
            call.opening_at,
            call.budget_total_eur,
            call.budget_per_project_min_eur,
            call.budget_per_project_max_eur,
            call.trl_min,
            call.trl_max,
            list(call.topic_keywords),
            list(call.sectors),
            list(call.geo_scope),
            list(call.eligibility_tags),
            json.dumps(call.eligibility_summary),
            json.dumps(call.raw_metadata),
            lifecycle,
            call.scope_summary,
            call.funding_rate_pct,
            call.application_form_url,
            call.work_programme_pdf_url,
            call.partner_consortium_required,
            call.source_url_canonical,
            call.agency_id,
        )
        assert row is not None
        return bool(row["inserted"])

    # ── scraper_runs bookkeeping ─────────────────────────────────────

    async def _insert_run_started(
        self,
        *,
        source: CallSource,
        started_at: datetime,
        triggered_by: TriggeredBy,
        triggered_by_user_id: UUID | None,
    ) -> UUID:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO scraper_runs (
                  source, started_at, triggered_by, triggered_by_user_id
                ) VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                source,
                started_at,
                triggered_by,
                triggered_by_user_id,
            )
            assert row is not None
            return UUID(str(row["id"]))

    async def _update_run_finished(
        self, run_id: UUID, result: ScraperRunResult
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE scraper_runs SET
                  finished_at = $2,
                  calls_discovered = $3,
                  calls_persisted = $4,
                  calls_updated = $5,
                  calls_failed = $6,
                  duration_seconds = $7,
                  errors = $8::jsonb
                WHERE id = $1
                """,
                run_id,
                result.finished_at,
                result.calls_discovered,
                result.calls_persisted,
                result.calls_updated,
                result.calls_failed,
                round(result.duration_seconds, 3),
                json.dumps(result.errors),
            )


__all__ = ["ScraperRunner", "TriggeredBy"]
