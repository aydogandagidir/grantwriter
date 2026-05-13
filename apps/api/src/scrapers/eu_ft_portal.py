"""EU Funding & Tenders Portal (SEDIA) scraper.

Per docs/programs/eu_ft_portal.md, the SEDIA Search API is anonymous
(``apiKey=SEDIA`` query parameter, no OAuth) and the Topic Details API
serves enriched per-topic JSON. V1 covers Horizon Europe **RIA** and
**IA** only — CSA, MSCA, ERC, EIC, Digital Europe etc. land in Faz 7.

Endpoints:

- Search:        POST  ``https://api.tech.ec.europa.eu/search-api/prod/rest/search``
- Topic details: GET   ``https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/{id-lowercase}.json``

HTTP is mediated by an :class:`httpx.AsyncClient` injected through the
constructor so tests can substitute :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Final

import httpx

from src.scrapers import register_scraper
from src.scrapers.base import BaseScraper, NormalizedCall
from src.scrapers.normalization import (
    canonicalize_url,
    extract_trl_range,
    parse_budget_range,
)

logger = logging.getLogger(__name__)


# ── Endpoints / constants ────────────────────────────────────────────────

SEARCH_API_URL: Final = (
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
)
TOPIC_DETAILS_URL_TEMPLATE: Final = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/data/"
    "topicDetails/{tid}.json"
)
TOPIC_LANDING_URL_TEMPLATE: Final = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
    "screen/opportunities/topic-details/{tid}"
)

# F&T classification codes per the public schema (docs/programs/eu_ft_portal.md).
FRAMEWORK_PROGRAMME_HORIZON: Final = "43108390"
STATUS_FORTHCOMING: Final = "31094501"
STATUS_OPEN: Final = "31094502"

# Type-of-action → programme_id mapping. The full Horizon Europe action
# vocabulary (CSA, COFUND, ERC, EIC, MSCA, …) is documented but only
# RIA + IA have programme modules in Faz 1/5. Everything else falls
# through to ``None`` and is skipped by :meth:`normalize`.
TYPE_OF_ACTION_TO_PROGRAMME_ID: Final[dict[str, str]] = {
    "HORIZON-RIA": "horizon_eu_ria",
    "HORIZON-IA": "horizon_eu_ia",
}

USER_AGENT: Final = "BluedevGrantWriter/0.1 (+contact@bluedev.io)"
DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGES: Final = 20
"""Safety cap; the open-call corpus is rarely more than 4-5 pages of 50."""


# ── HTML helpers (kept local — only needed here) ─────────────────────────

_HTML_TAG_RE: Final = re.compile(r"<[^>]+>")
_WORK_PROGRAMME_RE: Final = re.compile(
    r'href="([^"]+work[-_]?programme[^"]*\.pdf)"', re.IGNORECASE
)


def _strip_html(html: str | None) -> str:
    """Coarse HTML → text. Good enough for the regex-based budget / TRL
    extractors in ``src/scrapers/normalization.py``."""

    if not html:
        return ""
    text = _HTML_TAG_RE.sub(" ", html)
    return " ".join(text.split())


def _first(x: Any) -> Any | None:
    """SEDIA Search returns most fields as one-element lists."""

    if isinstance(x, list):
        return x[0] if x else None
    return x


def _ensure_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _parse_iso(value: Any) -> date | None:
    """Tolerant ISO-date parser — accepts trailing time/timezone parts."""

    if not value or not isinstance(value, str):
        return None
    d_part = value.split("T")[0]
    try:
        return date.fromisoformat(d_part)
    except ValueError:
        return None


def _extract_type_of_action(detail: dict[str, Any]) -> str | None:
    """Walk ``actions[].types[].typeOfAction`` for the first non-empty entry."""

    actions = detail.get("actions") or []
    for action in actions:
        types = action.get("types") or []
        for t in types:
            toa = t.get("typeOfAction")
            if toa:
                return str(toa)
    return None


def _earliest_deadline(detail: dict[str, Any]) -> date | None:
    """The soonest of all ``actions[].deadlineDates[]`` values."""

    candidates: list[date] = []
    for action in detail.get("actions") or []:
        for dl in action.get("deadlineDates") or []:
            d = _parse_iso(dl)
            if d is not None:
                candidates.append(d)
    return min(candidates) if candidates else None


def _opening_date(detail: dict[str, Any]) -> date | None:
    for action in detail.get("actions") or []:
        d = _parse_iso(action.get("plannedOpeningDate"))
        if d is not None:
            return d
    return None


def _work_programme_url(detail: dict[str, Any]) -> str | None:
    """Heuristic: look in ``latestInfos[].content`` HTML for a PDF link
    whose URL contains 'work-programme' / 'work_programme'."""

    for info in detail.get("latestInfos") or []:
        content = info.get("content") or ""
        match = _WORK_PROGRAMME_RE.search(content)
        if match:
            return match.group(1)
    return None


# ── Scraper ──────────────────────────────────────────────────────────────


@register_scraper
class EUFTPortalScraper(BaseScraper):
    """Horizon Europe RIA + IA topic scraper for the SEDIA portal."""

    source = "eu_ft_portal"
    name = "EU Funding & Tenders Portal"
    # Multi-programme: each topic resolves to a programme via type-of-action.
    default_programme_id = None

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._page_size = page_size

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        """Release the underlying client if this scraper owns it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Search API: discovery ────────────────────────────────────────

    async def discover(self) -> AsyncIterator[dict[str, Any]]:
        """Paginate the SEDIA Search API and yield each open Horizon topic.

        Yields lightweight dicts with the topic identifier and the raw
        search-result metadata; downstream :meth:`fetch_call_detail`
        enriches them with the full topic-details JSON.
        """

        client = await self._get_client()
        page_number = 1
        seen = 0
        while page_number <= MAX_PAGES:
            params = {
                "apiKey": "SEDIA",
                "text": "*",
                "pageSize": str(self._page_size),
                "pageNumber": str(page_number),
            }
            # SEDIA expects an ElasticSearch DSL query string. We filter
            # to Horizon topics (type=1) that are Forthcoming or Open.
            es_query = {
                "bool": {
                    "must": [
                        {"terms": {"type": ["1"]}},
                        {"terms": {
                            "status": [STATUS_FORTHCOMING, STATUS_OPEN],
                        }},
                        {"terms": {
                            "frameworkProgramme": [FRAMEWORK_PROGRAMME_HORIZON],
                        }},
                    ]
                }
            }
            body = {"query": json.dumps(es_query), "languages": "en"}
            resp = await client.post(SEARCH_API_URL, params=params, data=body)
            resp.raise_for_status()
            payload = resp.json()

            results = payload.get("results") or []
            if not results:
                break
            for item in results:
                metadata = item.get("metadata") or {}
                identifier = (
                    _first(metadata.get("identifier"))
                    or _first(metadata.get("callIdentifier"))
                )
                if not identifier:
                    continue
                yield {
                    "external_id": str(identifier),
                    "search_metadata": item,
                }
                seen += 1

            total = int(payload.get("totalResults") or 0)
            if total and seen >= total:
                break
            if len(results) < self._page_size:
                break
            page_number += 1

        logger.info(
            "eu_ft_portal_discover_done",
            extra={"page_count": page_number, "topic_count": seen},
        )

    # ── Topic Details API: enrichment ────────────────────────────────

    async def fetch_call_detail(
        self,
        external_id: str,
        *,
        discover_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch enriched topic JSON. URL identifiers are case-sensitive
        and must be lowercased; the API 404s otherwise."""

        client = await self._get_client()
        topic_id_lower = external_id.lower()
        url = TOPIC_DETAILS_URL_TEMPLATE.format(tid=topic_id_lower)
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        # The endpoint wraps the topic in a "TopicDetails" envelope but
        # some legacy responses return the bare object — accept both.
        topic = payload.get("TopicDetails") or payload
        return {
            "external_id": external_id,
            "search_metadata": (discover_payload or {}).get("search_metadata", {}),
            "topic_details": topic,
        }

    # ── Normalize: detail → NormalizedCall ───────────────────────────

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        external_id = raw["external_id"]
        detail: dict[str, Any] = raw.get("topic_details") or {}
        search_meta: dict[str, Any] = (
            raw.get("search_metadata", {}).get("metadata") or {}
        )

        toa = _extract_type_of_action(detail)
        programme_id = TYPE_OF_ACTION_TO_PROGRAMME_ID.get(toa or "")
        if programme_id is None:
            # Topic falls outside the RIA+IA scope (CSA / MSCA / ERC / …).
            # Raise so the runner skips it without persisting an
            # FK-violation row; logs the type-of-action for diagnostics.
            raise ValueError(
                f"Unsupported Horizon type-of-action {toa!r} for {external_id}"
            )

        # Title — prefer the structured detail, fall back to search.
        title = (
            detail.get("title")
            or _first(search_meta.get("callTitle"))
            or external_id
        )

        # Description (HTML) → plaintext for regex-based field extraction.
        description_html = (
            detail.get("description")
            or _first(search_meta.get("descriptionByte"))
            or ""
        )
        description_text = _strip_html(description_html)

        budget_min, budget_max, _currency = parse_budget_range(
            description_text, default_currency="EUR"
        )
        trl_min, trl_max = extract_trl_range(description_text)

        deadline = _earliest_deadline(detail)
        opening_at = _opening_date(detail)

        keywords_raw = detail.get("keywords") or _ensure_list(
            search_meta.get("keywords")
        )
        topic_keywords = [str(k) for k in keywords_raw[:30]]

        landing_url = TOPIC_LANDING_URL_TEMPLATE.format(tid=external_id.lower())

        # Funding rate per docs/programs/eu_ft_portal.md:
        #   RIA → 100% all entities
        #   IA  → 70% for-profit, 100% non-profit (we surface the
        #          for-profit rate, which is the binding constraint
        #          for SME applicants)
        funding_rate_pct = 100 if programme_id == "horizon_eu_ria" else 70

        return NormalizedCall(
            source="eu_ft_portal",
            external_id=external_id,
            programme_id=programme_id,
            agency_id=toa,
            title=str(title),
            scope_summary=description_text[:1000] or None,
            call_text=description_text or None,
            language="en",
            call_url=landing_url,
            source_url_canonical=canonicalize_url(landing_url),
            work_programme_pdf_url=_work_programme_url(detail),
            opening_at=opening_at,
            deadline=deadline,
            budget_per_project_min_eur=budget_min,
            budget_per_project_max_eur=budget_max,
            funding_rate_pct=funding_rate_pct,
            trl_min=trl_min,
            trl_max=trl_max,
            topic_keywords=topic_keywords,
            sectors=[],
            geo_scope=["eu27", "assoc"],
            eligibility_tags=[
                "sme",
                "university",
                "research_org",
                "ngo",
                "large_corp",
                "consortium_required",
            ],
            eligibility_summary={
                "consortium_min_partners": 3,
                "consortium_min_countries": 3,
                "sme_eligible": True,
                "individual_eligible": False,
            },
            partner_consortium_required=True,
            raw_metadata={
                "type_of_action": toa,
                "framework_programme_id": (
                    detail.get("frameworkProgramme") or {}
                ).get("id"),
                "framework_programme_abbreviation": (
                    detail.get("frameworkProgramme") or {}
                ).get("abbreviation"),
                "call_identifier": detail.get("callIdentifier"),
                "ccm2_id": detail.get("ccm2Id"),
                "sme_flag_topic_specific": detail.get("sme"),
            },
        )


__all__ = ["EUFTPortalScraper"]
