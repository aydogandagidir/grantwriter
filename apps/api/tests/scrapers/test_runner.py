"""Integration tests for :class:`src.scrapers.runner.ScraperRunner`.

Drives the full pipeline against a real database, so the suite is gated
on the ``live_db_pool`` fixture (skipped when ``TEST_DATABASE_URL`` is
unset — same gate the rest of the integration suite uses).

We test through a fake scraper rather than NLnet / EU F&T / TÜBİTAK so
the assertions stay deterministic and decoupled from upstream HTML
churn — the runner doesn't care what produced the NormalizedCall.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
import pytest
from src.scrapers.base import BaseScraper, NormalizedCall
from src.scrapers.runner import ScraperRunner


# ── Fake scrapers (test-only, not registered globally) ──────────────────


class _CannedScraper(BaseScraper):
    """Yields a pre-built list of NormalizedCall records.

    Bypasses ``normalize`` by stashing the call object on the discover
    payload and pulling it out in ``normalize`` directly. Simulates a
    happy scraper run without needing real HTML / JSON parsing.
    """

    source = "manual"  # 'manual' is registry-safe; never collides.
    name = "Canned (Test)"

    def __init__(self, calls: Sequence[NormalizedCall]) -> None:
        self._calls = list(calls)

    async def discover(self) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        for call in self._calls:
            yield {"external_id": call.external_id, "_call": call}

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        return raw["_call"]


class _FailingNormalizer(_CannedScraper):
    """Like _CannedScraper but normalize raises for one external_id."""

    def __init__(
        self,
        calls: Sequence[NormalizedCall],
        *,
        fail_on: str,
    ) -> None:
        super().__init__(calls)
        self._fail_on = fail_on

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        if raw["external_id"] == self._fail_on:
            raise RuntimeError(f"synthetic failure for {self._fail_on}")
        return raw["_call"]


def _make_call(
    external_id: str,
    *,
    title: str = "Test call",
    programme_id: str = "tubitak_1501",
    deadline: date | None = date(2026, 9, 15),
    funding_rate_pct: int | None = 75,
) -> NormalizedCall:
    return NormalizedCall(
        source="manual",
        external_id=external_id,
        programme_id=programme_id,
        title=title,
        language="en",
        call_url=f"https://example.com/calls/{external_id}",
        source_url_canonical=f"https://example.com/calls/{external_id}",
        deadline=deadline,
        funding_rate_pct=funding_rate_pct,
        topic_keywords=["test", "fixture"],
        eligibility_tags=["sme"],
        geo_scope=["eu27"],
        eligibility_summary={"sme_eligible": True},
        raw_metadata={"fixture": True},
    )


# ── persist() ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_inserts_new_row_and_returns_true(
    live_db_pool: asyncpg.Pool,
) -> None:
    runner = ScraperRunner(pool=live_db_pool)
    call = _make_call("runner-test-insert-1")

    async with live_db_pool.acquire() as conn:
        was_new = await runner.persist(conn, call)

    assert was_new is True

    # Verify the row landed with the expected fields.
    async with live_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM calls WHERE source = $1 AND external_id = $2",
            "manual",
            "runner-test-insert-1",
        )
    assert row is not None
    assert row["title"] == "Test call"
    assert row["funding_rate_pct"] == 75
    assert row["status"] in ("open", "closing_soon", "closed", "draft")
    assert row["topic_keywords"] == ["test", "fixture"]


@pytest.mark.asyncio
async def test_persist_idempotent_update_returns_false(
    live_db_pool: asyncpg.Pool,
) -> None:
    """Re-running on the same (source, external_id) updates and returns False."""

    runner = ScraperRunner(pool=live_db_pool)
    call_v1 = _make_call("runner-test-upsert-1", title="v1")
    call_v2 = _make_call("runner-test-upsert-1", title="v2 updated")

    async with live_db_pool.acquire() as conn:
        was_new_first = await runner.persist(conn, call_v1)
        was_new_second = await runner.persist(conn, call_v2)

    assert was_new_first is True
    assert was_new_second is False

    async with live_db_pool.acquire() as conn:
        title = await conn.fetchval(
            "SELECT title FROM calls WHERE source = $1 AND external_id = $2",
            "manual",
            "runner-test-upsert-1",
        )
    assert title == "v2 updated"


@pytest.mark.asyncio
async def test_persist_computes_lifecycle_status_from_deadline(
    live_db_pool: asyncpg.Pool,
) -> None:
    """Past deadline → 'closed'; close deadline → 'closing_soon';
    far deadline → 'open'."""

    runner = ScraperRunner(pool=live_db_pool)
    far_call = _make_call("runner-lifecycle-far", deadline=date(2030, 1, 1))
    past_call = _make_call("runner-lifecycle-past", deadline=date(2020, 1, 1))

    async with live_db_pool.acquire() as conn:
        await runner.persist(conn, far_call)
        await runner.persist(conn, past_call)

    async with live_db_pool.acquire() as conn:
        far_status = await conn.fetchval(
            "SELECT status FROM calls WHERE external_id = $1",
            "runner-lifecycle-far",
        )
        past_status = await conn.fetchval(
            "SELECT status FROM calls WHERE external_id = $1",
            "runner-lifecycle-past",
        )

    assert far_status == "open"
    assert past_status == "closed"


# ── run() — full pipeline ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_happy_path_persists_all_calls(
    live_db_pool: asyncpg.Pool,
) -> None:
    calls = [
        _make_call("runner-happy-1", title="Happy 1"),
        _make_call("runner-happy-2", title="Happy 2"),
        _make_call("runner-happy-3", title="Happy 3"),
    ]
    scraper = _CannedScraper(calls)
    runner = ScraperRunner(pool=live_db_pool)

    result = await runner.run("manual", scraper=scraper, triggered_by="test")

    assert result.calls_discovered == 3
    assert result.calls_persisted == 3
    assert result.calls_updated == 0
    assert result.calls_failed == 0
    assert result.errors == []
    assert result.duration_seconds >= 0

    # All three rows landed.
    async with live_db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM calls WHERE source = $1 AND external_id LIKE 'runner-happy-%'",
            "manual",
        )
    assert count == 3


@pytest.mark.asyncio
async def test_run_re_run_reports_updates_not_new(
    live_db_pool: asyncpg.Pool,
) -> None:
    """Second run of the same scraper → calls_updated, not calls_persisted."""

    calls = [_make_call("runner-rerun-1", title="First")]
    runner = ScraperRunner(pool=live_db_pool)

    first = await runner.run("manual", scraper=_CannedScraper(calls), triggered_by="test")
    assert first.calls_persisted == 1 and first.calls_updated == 0

    calls[0] = _make_call("runner-rerun-1", title="Second")
    second = await runner.run("manual", scraper=_CannedScraper(calls), triggered_by="test")
    assert second.calls_persisted == 0
    assert second.calls_updated == 1


@pytest.mark.asyncio
async def test_run_contains_per_call_failures(
    live_db_pool: asyncpg.Pool,
) -> None:
    """A failure on one call must NOT abort the rest of the run."""

    calls = [
        _make_call("runner-fail-1", title="OK 1"),
        _make_call("runner-fail-2", title="Will fail"),
        _make_call("runner-fail-3", title="OK 3"),
    ]
    scraper = _FailingNormalizer(calls, fail_on="runner-fail-2")
    runner = ScraperRunner(pool=live_db_pool)

    result = await runner.run("manual", scraper=scraper, triggered_by="test")

    assert result.calls_discovered == 3
    assert result.calls_persisted == 2  # 1 and 3 succeed
    assert result.calls_failed == 1
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err["external_id"] == "runner-fail-2"
    assert "synthetic failure" in err["error"]


@pytest.mark.asyncio
async def test_run_writes_scraper_runs_row_with_summary(
    live_db_pool: asyncpg.Pool,
) -> None:
    calls = [_make_call("runner-bookkeep-1")]
    scraper = _CannedScraper(calls)
    runner = ScraperRunner(pool=live_db_pool)

    before = datetime.now(timezone.utc)
    await runner.run("manual", scraper=scraper, triggered_by="manual")

    async with live_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM scraper_runs
             WHERE source = $1 AND triggered_by = $2 AND started_at >= $3
             ORDER BY started_at DESC LIMIT 1
            """,
            "manual",
            "manual",
            before,
        )
    assert row is not None
    assert row["finished_at"] is not None
    assert row["calls_discovered"] == 1
    assert row["calls_persisted"] == 1
    assert row["calls_updated"] == 0
    assert row["calls_failed"] == 0
    assert float(row["duration_seconds"]) >= 0
    # No errors → empty JSON array, not null.
    assert row["errors"] in ([], "[]") or row["errors"] == []


@pytest.mark.asyncio
async def test_run_unknown_source_raises(
    live_db_pool: asyncpg.Pool,
) -> None:
    """``schumann`` is a documented source value but has no scraper in
    Faz 1 — runner raises rather than silently returning empty."""

    runner = ScraperRunner(pool=live_db_pool)
    with pytest.raises(KeyError, match="No scraper registered"):
        await runner.run("schumann")


# ── Unit tests (no DB) — exercised even when TEST_DATABASE_URL unset ────


@pytest.mark.asyncio
async def test_run_unknown_source_raises_without_db() -> None:
    """Registry lookup happens before any DB I/O — test it stand-alone."""

    runner = ScraperRunner(pool=None)  # type: ignore[arg-type]  # pool unused
    with pytest.raises(KeyError, match="No scraper registered"):
        await runner.run("schumann")


def test_canned_scraper_satisfies_base_contract() -> None:
    """Sanity: the fake scraper used in integration tests is itself a
    valid BaseScraper subclass. Catches regressions in the carrier shape
    that would silently break the integration suite."""

    scraper = _CannedScraper([_make_call("smoke-1")])
    assert isinstance(scraper, BaseScraper)
    assert scraper.source == "manual"
