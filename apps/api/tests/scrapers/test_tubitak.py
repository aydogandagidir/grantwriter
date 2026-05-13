"""Unit tests for :mod:`src.scrapers.tubitak`.

The scraper hits a real public HTML endpoint, so tests use
:class:`httpx.MockTransport` and inline HTML fixtures that mirror the
``views-row`` markup observed on ``/tr/acik-cagrilar`` as of 2026-05-14.

Card structure (verbatim shape):

    <div class="c-baslik">
      <a href="/tr/destekler/destek/.../cagri-1501-2026-1-..." hreflang="tr">
        1501 Sanayi Ar-Ge 2026/1 Çağrısı
      </a>
    </div>
    <div class="c-kodu">#1501 2026-1</div>
    <div class="c-icerik">İlgili destek: <a href="...">1501 - ...</a></div>
    <span class="views-label ...">Başvuru aralığı</span>
    <div class="field-content">
      <time datetime="2026-01-01T00:00:00Z" ...>01 Oca 2026 - 00:00</time>
       -
      <time datetime="2026-03-30T20:59:00Z" ...>30 Mar 2026 - 23:59</time>
    </div>
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from src.scrapers import SCRAPER_REGISTRY, get_scraper
from src.scrapers.base import NormalizedCall
from src.scrapers.tubitak import (
    LISTING_URL,
    PROGRAM_CODE_TO_PROGRAMME_ID,
    TUBITAKScraper,
    _build_external_id,
    _parse_cards,
    _parse_deadline_from_time_entries,
)

# ── Inline HTML fixtures (trimmed but byte-faithful to the real funder) ──


def _card_html(
    *,
    code: str,
    slug: str,
    title: str,
    cycle: str | None,
    opening_iso: str,
    deadline_iso: str,
) -> str:
    cycle_div = (
        f'<div class="c-kodu">#{code} {cycle}</div>'
        if cycle
        else '<div class="c-kodu">#</div>'
    )
    return (
        '<div class="views-row">'
        '<div class="views-field views-field-title">'
        '<span class="field-content">'
        '<div class="c-baslik">'
        f'<a href="/tr/destekler/destek/sanayi/ulusal-destek-programlari/cagri-{code}-{slug}" hreflang="tr">'
        f"{title}</a></div>"
        + cycle_div
        + '<div class="c-icerik">İlgili destek: '
        f'<a href="/tr/destekler/sanayi/ulusal-destek-programlari/{code}-{slug}" hreflang="tr">'
        f"{code} - {title}</a></div>"
        "</span></div>"
        '<div class="views-field views-field-field-basvuru-araligi">'
        '<span class="views-label views-label-field-basvuru-araligi">Başvuru aralığı</span>'
        '<div class="field-content">'
        f'<time datetime="{opening_iso}" class="datetime">opening</time>'
        " - "
        f'<time datetime="{deadline_iso}" class="datetime">deadline</time>'
        "</div></div></div>"
    )


def _listing_html(cards: list[str]) -> str:
    """Wrap the cards in just enough page chrome that the splitter
    correctly drops the header before card 1."""

    header = (
        '<!DOCTYPE html><html><body>'
        '<div class="view-header">'
        '<div class="c-baslik">Açık Çağrılar</div>'  # NOTE: header c-baslik
        "</div>"
        '<div class="view-content">'
    )
    return header + "".join(cards) + "</div></body></html>"


_CARD_1501 = _card_html(
    code="1501",
    slug="2026-1-cagrisi",
    title="1501 Sanayi Ar-Ge 2026/1 Çağrısı",
    cycle="2026-1",
    opening_iso="2026-01-01T00:00:00Z",
    deadline_iso="2026-03-30T20:59:00Z",
)

_CARD_1507 = _card_html(
    code="1507",
    slug="kobi-arge-baslangic-2026-1",
    title="1507 KOBİ Ar-Ge Başlangıç 2026/1",
    cycle="2026-1",
    opening_iso="2026-01-01T00:00:00Z",
    deadline_iso="2026-03-30T20:59:00Z",
)

_CARD_UNKNOWN_1831 = _card_html(
    code="1831",
    slug="yesil-inovasyon-mentorluk",
    title="1831 Yeşil İnovasyon Teknoloji Mentörlük Çağrısı",
    cycle=None,
    opening_iso="2024-05-15T21:00:00Z",
    deadline_iso="2030-01-01T20:59:00Z",
)


# ── _parse_deadline_from_time_entries ────────────────────────────────────


def test_parse_deadline_picks_last_time_entry() -> None:
    """Cards have two ``<time>`` entries: opening then deadline. We
    want the second."""

    deadline = _parse_deadline_from_time_entries(_CARD_1501)
    assert deadline == date(2026, 3, 30)


def test_parse_deadline_returns_none_when_no_time_entries() -> None:
    assert _parse_deadline_from_time_entries("<div>no time</div>") is None


# ── _parse_cards ─────────────────────────────────────────────────────────


def test_parse_cards_returns_one_record_per_views_row() -> None:
    html = _listing_html([_CARD_1501, _CARD_1507])
    cards = _parse_cards(html)
    assert len(cards) == 2
    assert cards[0]["program_code"] == "1501"
    assert cards[1]["program_code"] == "1507"


def test_parse_cards_captures_cycle_when_present() -> None:
    cards = _parse_cards(_listing_html([_CARD_1501]))
    assert cards[0]["cycle"] == "2026-1"


def test_parse_cards_cycle_none_when_absent() -> None:
    cards = _parse_cards(_listing_html([_CARD_UNKNOWN_1831]))
    assert cards[0]["cycle"] is None


def test_parse_cards_resolves_absolute_detail_url() -> None:
    cards = _parse_cards(_listing_html([_CARD_1501]))
    assert cards[0]["detail_url"].startswith("https://tubitak.gov.tr/")
    assert "cagri-1501" in cards[0]["detail_url"]


def test_parse_cards_extracts_deadline_as_date() -> None:
    cards = _parse_cards(_listing_html([_CARD_1501]))
    assert cards[0]["deadline"] == date(2026, 3, 30)


def test_parse_cards_skips_header_block() -> None:
    """The header section also contains <div class='c-baslik'> for the
    page heading. The splitter must drop it (segments[0])."""

    html = _listing_html([_CARD_1501])
    # Header c-baslik has no anchor with a /tr/destekler/destek/ href,
    # so even if it slipped through, the anchor regex would reject it.
    cards = _parse_cards(html)
    assert len(cards) == 1  # not 2 — header didn't sneak in


# ── _build_external_id ──────────────────────────────────────────────────


def test_build_external_id_uses_cycle_when_present() -> None:
    card = {"program_code": "1501", "cycle": "2026-1", "deadline_iso": "2026-03-30"}
    assert _build_external_id(card) == "1501-2026-1"


def test_build_external_id_falls_back_to_deadline() -> None:
    card = {"program_code": "1831", "cycle": None, "deadline_iso": "2030-01-01"}
    assert _build_external_id(card) == "1831-2030-01-01"


def test_build_external_id_program_code_only_when_no_dates() -> None:
    card = {"program_code": "1501", "cycle": None, "deadline_iso": None}
    assert _build_external_id(card) == "1501"


# ── Registry ────────────────────────────────────────────────────────────


def test_tubitak_scraper_registers_under_source() -> None:
    assert SCRAPER_REGISTRY.get("tubitak") is TUBITAKScraper
    assert get_scraper("tubitak") is TUBITAKScraper


def test_program_code_map_covers_documented_programmes() -> None:
    """Faz 1.3 ships 1501+1507 active and seeds 1505/1601/1512/1071/2244
    programmes table rows. The scraper map must include all seven."""

    assert set(PROGRAM_CODE_TO_PROGRAMME_ID) == {
        "1501", "1507", "1505", "1601", "1512", "1071", "2244",
    }


# ── HTTP MockTransport ──────────────────────────────────────────────────


def _client_serving(html: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── discover() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_emits_only_known_program_codes() -> None:
    html = _listing_html([_CARD_1501, _CARD_1507, _CARD_UNKNOWN_1831])
    async with _client_serving(html) as client:
        scraper = TUBITAKScraper(client=client)
        records: list[dict[str, Any]] = []
        async for r in scraper.discover():
            records.append(r)
    # 1831 is intentionally not in the program-code map yet → skipped.
    assert [r["card"]["program_code"] for r in records] == ["1501", "1507"]
    assert records[0]["external_id"] == "1501-2026-1"


@pytest.mark.asyncio
async def test_discover_empty_listing_yields_nothing() -> None:
    async with _client_serving(_listing_html([])) as client:
        scraper = TUBITAKScraper(client=client)
        records = [r async for r in scraper.discover()]
    assert records == []


@pytest.mark.asyncio
async def test_discover_uses_configured_listing_url() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=_listing_html([_CARD_1501]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        scraper = TUBITAKScraper(client=client)
        async for _ in scraper.discover():
            pass
    assert requested == [LISTING_URL]


# ── normalize() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normalize_1501_maps_to_tubitak_1501() -> None:
    html = _listing_html([_CARD_1501])
    async with _client_serving(html) as client:
        scraper = TUBITAKScraper(client=client)
        raw = await anext(scraper.discover())
        call = await scraper.normalize(raw)

    assert isinstance(call, NormalizedCall)
    assert call.source == "tubitak"
    assert call.programme_id == "tubitak_1501"
    assert call.agency_id == "tubitak_1501"
    assert call.deadline == date(2026, 3, 30)
    assert call.opening_at == date(2026, 1, 1)
    assert call.funding_rate_pct == 75
    assert call.trl_min == 2 and call.trl_max == 7
    assert "TR" in call.eligibility_summary["countries"]
    assert call.eligibility_summary["max_projects_per_period"] == 2
    assert call.partner_consortium_required is False
    assert call.language == "tr"
    assert call.geo_scope == ["tr"]
    assert call.raw_metadata["cycle"] == "2026-1"
    assert call.external_id == "1501-2026-1"


@pytest.mark.asyncio
async def test_normalize_1507_imposes_sme_only_eligibility() -> None:
    html = _listing_html([_CARD_1507])
    async with _client_serving(html) as client:
        scraper = TUBITAKScraper(client=client)
        raw = await anext(scraper.discover())
        call = await scraper.normalize(raw)

    assert call.programme_id == "tubitak_1507"
    assert call.eligibility_tags == ["sme"]
    assert call.eligibility_summary["sme_status_required"] is True
    assert call.eligibility_summary["lifetime_proposal_cap"] == 5
    assert call.trl_max == 6  # narrower band than 1501


@pytest.mark.asyncio
async def test_normalize_preserves_detail_url_for_v2_enrichment() -> None:
    """V2 will fetch the detail page + PDF off this URL. Make sure V1
    preserves it intact (absolute, no query mangling)."""

    html = _listing_html([_CARD_1501])
    async with _client_serving(html) as client:
        scraper = TUBITAKScraper(client=client)
        raw = await anext(scraper.discover())
        call = await scraper.normalize(raw)

    assert call.call_url.startswith("https://tubitak.gov.tr/tr/destekler/destek/")
    assert "cagri-1501-2026-1-cagrisi" in call.call_url
    # Canonical form drops fragments + sorts query — no fragments here,
    # so it should be byte-equal to the absolute call_url.
    assert call.source_url_canonical == call.call_url
