"""Unit tests for :mod:`src.scrapers.eu_ft_portal`.

All HTTP is mediated by :class:`httpx.MockTransport`, so the entire test
suite runs offline. Fixtures are inline (small JSON dicts) rather than
file-based — the EU F&T API response shape evolves and we'd rather
update fixtures by editing one Python value than juggling JSON files.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from src.scrapers import SCRAPER_REGISTRY, get_scraper
from src.scrapers.base import NormalizedCall
from src.scrapers.eu_ft_portal import (
    EUFTPortalScraper,
    SEARCH_API_URL,
    TOPIC_DETAILS_URL_TEMPLATE,
    _earliest_deadline,
    _extract_type_of_action,
    _first,
    _opening_date,
    _parse_iso,
    _strip_html,
    _work_programme_url,
)


# ── Sample API responses ─────────────────────────────────────────────────


def _search_page(items: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """Build a SEDIA Search API response envelope."""

    return {
        "apiVersion": "2.146",
        "terms": "*",
        "responseTime": 12,
        "totalResults": total,
        "pageNumber": 1,
        "pageSize": len(items),
        "sort": "",
        "groupByField": "",
        "results": items,
    }


def _search_item(identifier: str, title: str) -> dict[str, Any]:
    """One element of ``Search API results[]``. SEDIA wraps most fields
    in single-element lists — mirror that."""

    return {
        "metadata": {
            "identifier": [identifier],
            "callIdentifier": [identifier.rsplit("-", 1)[0]],
            "callTitle": [title],
            "frameworkProgramme": ["43108390"],
            "descriptionByte": [
                "<p>Indicative budget €4-8 million. TRL 5-8 expected.</p>"
            ],
            "keywords": ["quantum"],
        }
    }


_TOPIC_DETAIL_RIA: dict[str, Any] = {
    "TopicDetails": {
        "ccm2Id": "11111",
        "identifier": "HORIZON-CL4-2026-EXAMPLE-RIA-01",
        "title": "Quantum communications networks",
        "callIdentifier": "HORIZON-CL4-2026-EXAMPLE-RIA",
        "frameworkProgramme": {"id": "43108390", "abbreviation": "HORIZON"},
        "keywords": ["quantum", "communications", "networks"],
        "sme": False,
        "actions": [
            {
                "status": {"id": "31094502", "abbreviation": "Open"},
                "types": [
                    {"typeOfAction": "HORIZON-RIA", "typeOfMGA": ["MGA"]}
                ],
                "plannedOpeningDate": "2026-01-15T00:00:00.000+0100",
                "deadlineDates": [
                    "2026-09-15T17:00:00.000+0200",
                    "2027-03-01T17:00:00.000+0100",  # Two-stage
                ],
                "submissionProcedure": "Two-stage",
            }
        ],
        "description": (
            "<p>The challenge is to build quantum networks at "
            "<strong>TRL 3-5</strong> with an indicative budget of "
            "<em>€2-8 million</em> per project.</p>"
        ),
        "latestInfos": [
            {
                "content": (
                    '<a href="https://example.com/wp/horizon-cl4-2026-'
                    'work-programme.pdf">WP PDF</a>'
                ),
                "lastChangeDate": "2026-04-04",
            }
        ],
    }
}


_TOPIC_DETAIL_IA: dict[str, Any] = {
    "TopicDetails": {
        "ccm2Id": "22222",
        "identifier": "HORIZON-CL4-2026-EXAMPLE-IA-01",
        "title": "Industrial AI for manufacturing",
        "callIdentifier": "HORIZON-CL4-2026-EXAMPLE-IA",
        "frameworkProgramme": {"id": "43108390", "abbreviation": "HORIZON"},
        "keywords": ["industrial", "ai", "manufacturing"],
        "sme": True,
        "actions": [
            {
                "status": {"id": "31094502", "abbreviation": "Open"},
                "types": [
                    {"typeOfAction": "HORIZON-IA", "typeOfMGA": ["MGA-LS"]}
                ],
                "plannedOpeningDate": "2026-02-01T00:00:00.000+0100",
                "deadlineDates": ["2026-11-30T17:00:00.000+0100"],
                "submissionProcedure": "Single-stage",
            }
        ],
        "description": (
            "<p>Innovation Action with TRL 5-8 target. "
            "Per-project budget €12.5-25M for large-scale demonstration.</p>"
        ),
        "latestInfos": [],
    }
}


_TOPIC_DETAIL_UNSUPPORTED_CSA: dict[str, Any] = {
    "TopicDetails": {
        "ccm2Id": "33333",
        "identifier": "HORIZON-CL4-2026-EXAMPLE-CSA-01",
        "title": "Coordination support for digital skills",
        "frameworkProgramme": {"id": "43108390", "abbreviation": "HORIZON"},
        "actions": [
            {
                "status": {"id": "31094502", "abbreviation": "Open"},
                "types": [{"typeOfAction": "HORIZON-CSA"}],
                "deadlineDates": ["2026-10-01T17:00:00.000+0200"],
            }
        ],
        "description": "<p>CSA topic.</p>",
    }
}


# ── HTTP mock helpers ────────────────────────────────────────────────────


def _make_mock_handler(
    search_pages: list[dict[str, Any]] | None = None,
    topic_details: dict[str, dict[str, Any]] | None = None,
):
    """Build a MockTransport handler that serves search + topic-details."""

    search_pages = search_pages or []
    topic_details = topic_details or {}
    search_call_count: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search-api/prod/rest/search"):
            n = search_call_count["n"]
            if n >= len(search_pages):
                return httpx.Response(200, json={"results": [], "totalResults": 0})
            page = search_pages[n]
            search_call_count["n"] += 1
            return httpx.Response(200, json=page)
        # Topic details: extract topic id from URL.
        if "topicDetails" in request.url.path:
            slug = request.url.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for key, payload in topic_details.items():
                if key.lower() == slug:
                    return httpx.Response(200, json=payload)
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(500, json={"error": "unexpected URL"})

    return handler


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── Helper-function tests (pure) ─────────────────────────────────────────


def test_strip_html_removes_tags_and_collapses_whitespace() -> None:
    html = "<p>Hello   <strong>world</strong>!</p>"
    assert _strip_html(html) == "Hello world !"


def test_strip_html_empty_returns_empty() -> None:
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


def test_first_unwraps_single_element_list() -> None:
    assert _first(["a", "b"]) == "a"
    assert _first(["only"]) == "only"
    assert _first([]) is None
    assert _first("scalar") == "scalar"
    assert _first(None) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-09-15", date(2026, 9, 15)),
        ("2026-09-15T17:00:00.000+0200", date(2026, 9, 15)),
        ("2026-09-15T00:00:00Z", date(2026, 9, 15)),
        ("not-a-date", None),
        ("", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_iso(value: Any, expected: date | None) -> None:
    assert _parse_iso(value) == expected


def test_extract_type_of_action_walks_nested_lists() -> None:
    detail = _TOPIC_DETAIL_RIA["TopicDetails"]
    assert _extract_type_of_action(detail) == "HORIZON-RIA"


def test_extract_type_of_action_returns_none_when_missing() -> None:
    assert _extract_type_of_action({}) is None
    assert _extract_type_of_action({"actions": []}) is None
    assert _extract_type_of_action({"actions": [{"types": []}]}) is None


def test_earliest_deadline_picks_min_across_actions() -> None:
    detail = _TOPIC_DETAIL_RIA["TopicDetails"]
    # Two-stage call with both stage-1 and stage-2 deadlines → earliest.
    assert _earliest_deadline(detail) == date(2026, 9, 15)


def test_earliest_deadline_none_when_no_deadlines() -> None:
    assert _earliest_deadline({"actions": [{"deadlineDates": []}]}) is None


def test_opening_date_from_first_action() -> None:
    detail = _TOPIC_DETAIL_RIA["TopicDetails"]
    assert _opening_date(detail) == date(2026, 1, 15)


def test_work_programme_url_extracted_from_latest_infos_html() -> None:
    detail = _TOPIC_DETAIL_RIA["TopicDetails"]
    url = _work_programme_url(detail)
    assert url is not None
    assert "work-programme.pdf" in url


def test_work_programme_url_none_when_no_link() -> None:
    assert _work_programme_url({"latestInfos": []}) is None
    assert _work_programme_url({}) is None


# ── Registry ────────────────────────────────────────────────────────────


def test_eu_ft_portal_scraper_registers_under_source() -> None:
    assert SCRAPER_REGISTRY.get("eu_ft_portal") is EUFTPortalScraper
    assert get_scraper("eu_ft_portal") is EUFTPortalScraper


def test_eu_ft_portal_class_attrs() -> None:
    assert EUFTPortalScraper.source == "eu_ft_portal"
    assert EUFTPortalScraper.name == "EU Funding & Tenders Portal"
    # Multi-programme — resolved per topic, not pinned at class level.
    assert EUFTPortalScraper.default_programme_id is None


# ── discover() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_yields_one_record_per_topic() -> None:
    page = _search_page(
        items=[
            _search_item("HORIZON-CL4-2026-EXAMPLE-RIA-01", "Quantum networks"),
            _search_item("HORIZON-CL4-2026-EXAMPLE-IA-01", "Industrial AI"),
        ],
        total=2,
    )
    handler = _make_mock_handler(search_pages=[page])
    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client, page_size=10)
        records: list[dict[str, Any]] = []
        async for r in scraper.discover():
            records.append(r)
    assert len(records) == 2
    assert records[0]["external_id"] == "HORIZON-CL4-2026-EXAMPLE-RIA-01"
    assert records[1]["external_id"] == "HORIZON-CL4-2026-EXAMPLE-IA-01"


@pytest.mark.asyncio
async def test_discover_handles_pagination_until_total_reached() -> None:
    """totalResults=3, pageSize=2 → 2 pages requested, 3 yields."""

    page1 = _search_page(
        items=[
            _search_item("HORIZON-A-01", "A"),
            _search_item("HORIZON-A-02", "B"),
        ],
        total=3,
    )
    page2 = _search_page(
        items=[_search_item("HORIZON-A-03", "C")],
        total=3,
    )
    handler = _make_mock_handler(search_pages=[page1, page2])
    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client, page_size=2)
        ids = [r["external_id"] async for r in scraper.discover()]
    assert ids == ["HORIZON-A-01", "HORIZON-A-02", "HORIZON-A-03"]


@pytest.mark.asyncio
async def test_discover_skips_items_without_identifier() -> None:
    page = _search_page(
        items=[
            {"metadata": {}},  # malformed — no identifier
            _search_item("HORIZON-OK-01", "ok"),
        ],
        total=2,
    )
    handler = _make_mock_handler(search_pages=[page])
    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client, page_size=10)
        ids = [r["external_id"] async for r in scraper.discover()]
    assert ids == ["HORIZON-OK-01"]


@pytest.mark.asyncio
async def test_discover_empty_response_terminates() -> None:
    handler = _make_mock_handler(
        search_pages=[_search_page(items=[], total=0)]
    )
    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client)
        ids = [r["external_id"] async for r in scraper.discover()]
    assert ids == []


# ── fetch_call_detail() ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_call_detail_lowercases_url() -> None:
    """The API 404s when the identifier isn't lowercased — verify our
    scraper does the conversion automatically."""

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json=_TOPIC_DETAIL_RIA)

    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client)
        await scraper.fetch_call_detail("HORIZON-CL4-2026-EXAMPLE-RIA-01")

    assert requested_paths
    last = requested_paths[-1]
    assert "horizon-cl4-2026-example-ria-01.json" in last
    assert "HORIZON" not in last  # No upper-case residue


@pytest.mark.asyncio
async def test_fetch_call_detail_returns_envelope_with_search_meta() -> None:
    handler = _make_mock_handler(
        topic_details={
            "horizon-cl4-2026-example-ria-01": _TOPIC_DETAIL_RIA,
        }
    )
    async with _client_with(handler) as client:
        scraper = EUFTPortalScraper(client=client)
        search_meta = {"metadata": {"keywords": ["q"]}}
        raw = await scraper.fetch_call_detail(
            "HORIZON-CL4-2026-EXAMPLE-RIA-01",
            discover_payload={"search_metadata": search_meta},
        )
    assert raw["external_id"] == "HORIZON-CL4-2026-EXAMPLE-RIA-01"
    assert raw["search_metadata"] == search_meta
    assert raw["topic_details"]["title"] == "Quantum communications networks"


# ── normalize() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normalize_horizon_ria_maps_correctly() -> None:
    raw = {
        "external_id": "HORIZON-CL4-2026-EXAMPLE-RIA-01",
        "topic_details": _TOPIC_DETAIL_RIA["TopicDetails"],
        "search_metadata": {"metadata": {}},
    }
    scraper = EUFTPortalScraper(client=httpx.AsyncClient())
    try:
        call = await scraper.normalize(raw)
    finally:
        await scraper.aclose()

    assert isinstance(call, NormalizedCall)
    assert call.source == "eu_ft_portal"
    assert call.programme_id == "horizon_eu_ria"
    assert call.agency_id == "HORIZON-RIA"
    assert call.funding_rate_pct == 100  # RIA = full-cost
    assert call.deadline == date(2026, 9, 15)  # earliest of two stages
    assert call.opening_at == date(2026, 1, 15)
    assert call.trl_min == 3 and call.trl_max == 5
    assert call.budget_per_project_min_eur == pytest.approx(2_000_000)
    assert call.budget_per_project_max_eur == pytest.approx(8_000_000)
    assert "consortium_required" in call.eligibility_tags
    assert call.partner_consortium_required is True
    assert call.work_programme_pdf_url is not None
    assert "work-programme.pdf" in call.work_programme_pdf_url
    assert call.raw_metadata["type_of_action"] == "HORIZON-RIA"


@pytest.mark.asyncio
async def test_normalize_horizon_ia_uses_70_pct_funding_rate() -> None:
    raw = {
        "external_id": "HORIZON-CL4-2026-EXAMPLE-IA-01",
        "topic_details": _TOPIC_DETAIL_IA["TopicDetails"],
        "search_metadata": {"metadata": {}},
    }
    scraper = EUFTPortalScraper(client=httpx.AsyncClient())
    try:
        call = await scraper.normalize(raw)
    finally:
        await scraper.aclose()

    assert call.programme_id == "horizon_eu_ia"
    assert call.agency_id == "HORIZON-IA"
    # IA funding rate per docs/programs/eu_ft_portal.md: 70% for-profit.
    assert call.funding_rate_pct == 70
    assert call.trl_min == 5 and call.trl_max == 8
    assert call.budget_per_project_min_eur == pytest.approx(12_500_000)
    assert call.budget_per_project_max_eur == pytest.approx(25_000_000)


@pytest.mark.asyncio
async def test_normalize_rejects_unsupported_type_of_action() -> None:
    """CSA / MSCA / EIC etc. don't have programme modules in Faz 1+5;
    normalize raises so the runner skips the call without an FK error."""

    raw = {
        "external_id": "HORIZON-CL4-2026-EXAMPLE-CSA-01",
        "topic_details": _TOPIC_DETAIL_UNSUPPORTED_CSA["TopicDetails"],
        "search_metadata": {"metadata": {}},
    }
    scraper = EUFTPortalScraper(client=httpx.AsyncClient())
    try:
        with pytest.raises(ValueError, match="Unsupported Horizon type-of-action"):
            await scraper.normalize(raw)
    finally:
        await scraper.aclose()


@pytest.mark.asyncio
async def test_normalize_canonicalises_landing_url() -> None:
    raw = {
        "external_id": "HORIZON-CL4-2026-EXAMPLE-RIA-01",
        "topic_details": _TOPIC_DETAIL_RIA["TopicDetails"],
        "search_metadata": {"metadata": {}},
    }
    scraper = EUFTPortalScraper(client=httpx.AsyncClient())
    try:
        call = await scraper.normalize(raw)
    finally:
        await scraper.aclose()

    assert call.call_url.endswith("horizon-cl4-2026-example-ria-01")
    assert call.source_url_canonical == call.call_url
