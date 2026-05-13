"""Foundation contract tests for :mod:`src.scrapers`.

Mirrors ``tests/programs/test_registry.py`` — the plug-and-play promise
(``new scraper = new module + one registry entry``) is only credible if
the interface itself is well-typed and every registered scraper conforms.
Concrete scrapers (eu_ft_portal, nlnet, …) ship in Faz 1; this file
verifies the abstractions work with a fake scraper today, so Faz 1 lands
on green tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

import pytest
from src.scrapers import (
    SCRAPER_REGISTRY,
    BaseScraper,
    NormalizedCall,
    ScraperRunResult,
    get_scraper,
    register_scraper,
)


# ── Helpers: fake scraper for the contract tests ─────────────────────────


class _FakeScraper(BaseScraper):
    """Minimal scraper that returns one hard-coded call.

    Not registered globally — tests instantiate it directly so they
    don't pollute :data:`SCRAPER_REGISTRY` for other tests.
    """

    source = "manual"  # 'manual' is the registry-safe slot — never collides
    name = "Fake (Test)"
    default_programme_id = "tubitak_1501"

    async def discover(self) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        yield {"external_id": "fake-001", "title": "Smoke test call"}

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        return NormalizedCall(
            source=self.source,
            external_id=raw["external_id"],
            programme_id=self.default_programme_id or "tubitak_1501",
            title=raw["title"],
            call_url="https://example.com/call/fake-001",
            language="tr",
            deadline=date(2026, 9, 15),
            trl_min=4,
            trl_max=7,
            topic_keywords=["ai", "industrial"],
        )


# ── NormalizedCall ───────────────────────────────────────────────────────


def test_normalized_call_minimal_construct() -> None:
    """Bare-minimum fields produce a valid NormalizedCall."""

    call = NormalizedCall(
        source="manual",
        external_id="ext-1",
        programme_id="tubitak_1501",
        title="Sanayi Ar-Ge Çağrısı 2026/1",
        call_url="https://example.com",
    )
    assert call.source == "manual"
    assert call.title.startswith("Sanayi")
    assert call.topic_keywords == []
    assert call.sectors == []
    assert call.eligibility_tags == []
    assert isinstance(call.scraped_at, datetime)


def test_normalized_call_is_frozen() -> None:
    """``frozen=True`` blocks accidental mutation of a NormalizedCall."""

    call = NormalizedCall(
        source="manual",
        external_id="ext-1",
        programme_id="tubitak_1501",
        title="t",
        call_url="https://example.com",
    )
    with pytest.raises(Exception):  # noqa: B017 — pydantic raises ValidationError on frozen
        call.title = "mutated"  # type: ignore[misc]


def test_normalized_call_rejects_extra_fields() -> None:
    """``extra=forbid`` catches typos like ``call_urls`` → ``call_url``."""

    with pytest.raises(Exception):  # noqa: B017
        NormalizedCall(
            source="manual",
            external_id="ext-1",
            programme_id="tubitak_1501",
            title="t",
            call_url="https://example.com",
            call_urls="oops",  # type: ignore[call-arg]
        )


def test_normalized_call_accepts_all_documented_sources() -> None:
    """Every source enum value documented in the migration must be
    constructable here — otherwise a scraper can't ever emit it."""

    documented_sources = [
        "eu_ft_portal", "nlnet", "cascade", "tubitak", "kosgeb",
        "eurostars", "schumann", "manual",
    ]
    for source in documented_sources:
        call = NormalizedCall(
            source=source,  # type: ignore[arg-type]
            external_id="x",
            programme_id="tubitak_1501",
            title="t",
            call_url="https://example.com",
        )
        assert call.source == source


def test_eligibility_tags_validate_against_literal() -> None:
    """Unknown eligibility tags are rejected — keeps the enum honest."""

    with pytest.raises(Exception):  # noqa: B017
        NormalizedCall(
            source="manual",
            external_id="ext-1",
            programme_id="tubitak_1501",
            title="t",
            call_url="https://example.com",
            eligibility_tags=["bogus_tag"],  # type: ignore[list-item]
        )


# ── BaseScraper roundtrip ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_scraper_discovers_and_normalizes() -> None:
    """Smoke test: discover → fetch_call_detail → normalize round-trip
    on a fake scraper. Concrete scrapers (Faz 1) get their own tests."""

    scraper = _FakeScraper()
    discovered: list[dict[str, Any]] = []
    async for record in scraper.discover():
        discovered.append(record)
    assert len(discovered) == 1

    raw = await scraper.fetch_call_detail(
        discovered[0]["external_id"], discover_payload=discovered[0]
    )
    assert raw["external_id"] == "fake-001"
    assert raw["title"] == "Smoke test call"

    normalized = await scraper.normalize(raw)
    assert normalized.title == "Smoke test call"
    assert normalized.programme_id == "tubitak_1501"
    assert normalized.trl_min == 4 and normalized.trl_max == 7


@pytest.mark.asyncio
async def test_fetch_call_detail_passthrough_when_no_override() -> None:
    """Default implementation of ``fetch_call_detail`` returns the
    discover payload as-is. Concrete sources with detail pages override
    this; sources where the index already has full data don't have to."""

    scraper = _FakeScraper()
    payload = {"external_id": "ext-1", "title": "via-payload"}
    raw = await scraper.fetch_call_detail("ext-1", discover_payload=payload)
    assert raw == payload
    # Different instance: returned dict must not be the same object
    # (subclasses sometimes mutate it).
    assert raw is not payload


@pytest.mark.asyncio
async def test_fetch_call_detail_without_payload_returns_id() -> None:
    """When discover didn't pass a payload, default impl returns
    a stub with just the external_id so the contract isn't ambiguous."""

    scraper = _FakeScraper()
    raw = await scraper.fetch_call_detail("ext-2")
    assert raw == {"external_id": "ext-2"}


# ── Registry ─────────────────────────────────────────────────────────────


def test_registry_contains_each_registered_source() -> None:
    """Each registered scraper class declares the matching ``source``
    ClassVar. Concrete scrapers (eu_ft_portal, tubitak, …) land in
    later Faz 1 PRs and extend this set."""

    for source, cls in SCRAPER_REGISTRY.items():
        assert cls.source == source, f"registry key {source} ≠ class.source {cls.source}"


def test_register_scraper_decorator_adds_to_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decorator inserts the class and rejects duplicates."""

    monkeypatch.setattr("src.scrapers.SCRAPER_REGISTRY", {})

    @register_scraper
    class A(BaseScraper):  # type: ignore[misc] — decorator returns same class
        source = "manual"
        name = "A"

        async def discover(self) -> AsyncIterator[dict[str, Any]]:
            if False:
                yield {}

        async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
            return NormalizedCall(
                source="manual",
                external_id="x",
                programme_id="tubitak_1501",
                title="t",
                call_url="https://example.com",
            )

    from src.scrapers import SCRAPER_REGISTRY as live_registry  # re-read patched

    assert live_registry["manual"] is A

    # Duplicate registration fails loudly.
    with pytest.raises(ValueError, match="already registered"):
        register_scraper(A)


def test_get_scraper_raises_on_unknown_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source with no registered scraper raises KeyError. We pick
    ``schumann`` because it's a documented source value (in the CHECK
    constraint) but doesn't have a scraper in Faz 1 — Faz 7."""

    monkeypatch.setattr("src.scrapers.SCRAPER_REGISTRY", {})
    with pytest.raises(KeyError, match="No scraper registered"):
        get_scraper("schumann")


# ── ScraperRunResult ─────────────────────────────────────────────────────


def test_scraper_run_result_computes_duration() -> None:
    start = datetime(2026, 5, 13, 10, 0, 0)
    end = datetime(2026, 5, 13, 10, 0, 12, 500_000)
    result = ScraperRunResult(
        source="manual",
        started_at=start,
        finished_at=end,
        calls_discovered=5,
        calls_persisted=3,
        calls_updated=2,
    )
    assert result.duration_seconds == 12.5
    assert result.calls_persisted + result.calls_updated == 5
