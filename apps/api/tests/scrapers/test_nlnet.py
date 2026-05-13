"""Unit tests for :mod:`src.scrapers.nlnet`.

NLnet's V1 scraper is deterministic — no network — so these tests cover
the entire discover→normalize roundtrip plus the cycle-calculation
heuristic. The V2 cross-check against the Atom feed lands separately
and gets its own (network-mocked) tests.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scrapers import SCRAPER_REGISTRY, get_scraper
from src.scrapers.base import NormalizedCall
from src.scrapers.nlnet import (
    _ACTIVE_FUNDS,
    NLnetScraper,
    _compute_next_deadline,
)

# ── Cycle calculation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "today, expected",
    [
        # Within a window — return that same 1st.
        (date(2026, 2, 1), date(2026, 2, 1)),
        # Day after a deadline — jump to the next one.
        (date(2026, 2, 2), date(2026, 4, 1)),
        # Late in a window — same window's deadline still applies if
        # we haven't passed it (here we test 'before 1st of next-even').
        (date(2026, 5, 13), date(2026, 6, 1)),
        # Day after the final window of the year → next February.
        (date(2026, 12, 2), date(2027, 2, 1)),
        # New Year's Day → first window of the new year.
        (date(2027, 1, 1), date(2027, 2, 1)),
    ],
)
def test_compute_next_deadline(today: date, expected: date) -> None:
    assert _compute_next_deadline(today) == expected


def test_compute_next_deadline_defaults_to_today() -> None:
    # Without an argument the function uses date.today() — assert
    # the result is one of the six valid windows in the current or
    # next year (no exception).
    result = _compute_next_deadline()
    assert result.month in (2, 4, 6, 8, 10, 12)
    assert result.day == 1


# ── Active funds (sanity) ───────────────────────────────────────────────


def test_active_funds_cover_three_currently_open_programmes() -> None:
    """Per docs/programs/nlnet.md: Commons, Taler, Fediversity are open;
    NGI0 Core / Entrust are closed. The scraper must reflect that."""

    agency_ids = {f["agency_id"] for f in _ACTIVE_FUNDS}
    assert agency_ids == {
        "nlnet_ngi0_commons",
        "nlnet_ngi_taler",
        "nlnet_ngi_fediversity",
    }


def test_active_funds_share_budget_band() -> None:
    """All three funds advertise the same 5k-50k band per the funder."""

    for fund in _ACTIVE_FUNDS:
        assert fund["budget_per_project_min_eur"] == 5_000.0
        assert fund["budget_per_project_max_eur"] == 50_000.0


# ── Registry ────────────────────────────────────────────────────────────


def test_nlnet_scraper_registers_under_source() -> None:
    """Import-time decorator should have populated the registry."""

    assert SCRAPER_REGISTRY.get("nlnet") is NLnetScraper
    assert get_scraper("nlnet") is NLnetScraper


def test_scraper_class_attrs() -> None:
    assert NLnetScraper.source == "nlnet"
    assert NLnetScraper.name == "NLnet Foundation"
    assert NLnetScraper.default_programme_id == "cascade_funding"


# ── discover ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_yields_one_record_per_active_fund() -> None:
    scraper = NLnetScraper(today=date(2026, 5, 13))
    records: list[dict[str, object]] = []
    async for r in scraper.discover():
        records.append(r)
    assert len(records) == 3
    assert {r["agency_id"] for r in records} == {
        "nlnet_ngi0_commons",
        "nlnet_ngi_taler",
        "nlnet_ngi_fediversity",
    }


@pytest.mark.asyncio
async def test_discover_deadline_matches_cycle_for_pinned_today() -> None:
    scraper = NLnetScraper(today=date(2026, 5, 13))
    async for r in scraper.discover():
        assert r["deadline"] == "2026-06-01"


# ── normalize ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normalize_produces_complete_normalized_call() -> None:
    scraper = NLnetScraper(today=date(2026, 5, 13))
    async for raw in scraper.discover():
        call = await scraper.normalize(raw)
        assert isinstance(call, NormalizedCall)
        assert call.source == "nlnet"
        assert call.programme_id == "cascade_funding"
        assert call.agency_id == raw["agency_id"]
        assert call.deadline == date(2026, 6, 1)
        # External id stable for the same cycle → idempotent upsert.
        assert call.external_id == f"{raw['agency_id']}-2026-06-01"
        # Title contains both fund name and human-readable deadline.
        assert raw["name"] in call.title
        assert "2026" in call.title
        # All NLnet calls are 100% funding and individual-eligible.
        assert call.funding_rate_pct == 100
        assert "individual" in call.eligibility_tags
        # Open-source licence requirement is captured for the eligibility
        # checker.
        assert call.eligibility_summary["open_source_licence_required"] is True


@pytest.mark.asyncio
async def test_normalize_external_id_is_stable_per_cycle() -> None:
    """Same fund + same cycle → identical external_id so upsert dedupes."""

    scraper = NLnetScraper(today=date(2026, 5, 13))
    raw = None
    async for r in scraper.discover():
        if r["agency_id"] == "nlnet_ngi0_commons":
            raw = r
            break
    assert raw is not None

    call_a = await scraper.normalize(raw)
    call_b = await scraper.normalize(raw)
    assert call_a.external_id == call_b.external_id


@pytest.mark.asyncio
async def test_normalize_distinct_external_ids_across_funds() -> None:
    scraper = NLnetScraper(today=date(2026, 5, 13))
    external_ids: set[str] = set()
    async for raw in scraper.discover():
        call = await scraper.normalize(raw)
        external_ids.add(call.external_id)
    assert len(external_ids) == 3


@pytest.mark.asyncio
async def test_normalize_emits_geo_scope_that_includes_associated() -> None:
    """Critical for Persona 3 (TR-based individual contributors) — NLnet
    explicitly accepts non-EU contributors with European dimension, so
    the matcher must not filter Turkish applicants out."""

    scraper = NLnetScraper(today=date(2026, 5, 13))
    async for raw in scraper.discover():
        call = await scraper.normalize(raw)
        assert "assoc" in call.geo_scope
        assert "eu27" in call.geo_scope
        break  # one is enough — they share the same scope


@pytest.mark.asyncio
async def test_normalize_external_id_changes_with_cycle() -> None:
    """Different deadline → different external_id, so a fresh row is
    created when the new cycle opens."""

    scraper_now = NLnetScraper(today=date(2026, 5, 13))
    scraper_later = NLnetScraper(today=date(2026, 7, 1))

    commons_now = None
    async for r in scraper_now.discover():
        if r["agency_id"] == "nlnet_ngi0_commons":
            commons_now = await scraper_now.normalize(r)
            break

    commons_later = None
    async for r in scraper_later.discover():
        if r["agency_id"] == "nlnet_ngi0_commons":
            commons_later = await scraper_later.normalize(r)
            break

    assert commons_now is not None and commons_later is not None
    assert commons_now.external_id != commons_later.external_id
    assert commons_now.deadline == date(2026, 6, 1)
    assert commons_later.deadline == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_normalize_records_evaluation_weights_in_metadata() -> None:
    """Faz 2 IdeaMatcher will surface 'why this fund' rationale that
    references NLnet's published weights (30/40/30). Make sure the
    scraper preserves them."""

    scraper = NLnetScraper(today=date(2026, 5, 13))
    async for raw in scraper.discover():
        call = await scraper.normalize(raw)
        weights = call.raw_metadata["evaluation_criteria_weights"]
        assert weights["technical_excellence"] == pytest.approx(0.30)
        assert weights["relevance_impact"] == pytest.approx(0.40)
        assert weights["cost_effectiveness"] == pytest.approx(0.30)
        break
