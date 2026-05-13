"""TÜBİTAK ulusal destek programları scraper.

Per docs/programs/tubitak.md the funder offers no public JSON API; we
scrape the open-calls listing at ``https://tubitak.gov.tr/tr/acik-cagrilar``
(Drupal CMS, server-rendered HTML). Each call appears as a ``views-row``
block whose ``<div class="c-baslik">`` anchor encodes both the program
code (``cagri-{NNNN}-{slug}``) and the human title; the application
window comes from two ``<time datetime="...">`` entries (start, end).

V1 limitations (lifted in V2/V3):

- **No detail-page enrichment**: budget / TRL / eligibility live in
  programme-specific PDFs that are sometimes scanned images (TÜBİTAK
  1507 2026/1 is). PDF + OCR fallback (Tesseract Turkish) ships later.
- **Unknown program codes are skipped**: only codes mapped in
  :data:`PROGRAM_CODE_TO_PROGRAMME_ID` produce calls; thematic 17xx /
  18xx identifiers (Yeşil Dönüşüm, SAYEM, …) are logged at INFO so we
  can expand the map deliberately.
- **Single listing page**: TÜBİTAK rarely paginates past one page of
  active calls; we cap at 100 cards as a safety net.

HTTP is mediated by :class:`httpx.AsyncClient` injected through the
constructor so tests substitute :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Final

import httpx

from src.scrapers import register_scraper
from src.scrapers.base import BaseScraper, NormalizedCall
from src.scrapers.normalization import canonicalize_url, parse_deadline

logger = logging.getLogger(__name__)


# ── Endpoints / constants ────────────────────────────────────────────────

LISTING_URL: Final = "https://tubitak.gov.tr/tr/acik-cagrilar"
BASE_URL: Final = "https://tubitak.gov.tr"
USER_AGENT: Final = "BluedevGrantWriter/0.1 (+contact@bluedev.io)"
DEFAULT_TIMEOUT: Final = 30.0
MAX_CARDS_PER_LISTING: Final = 100

# Mapping from TÜBİTAK 4-digit program code → programmes.id.
# Codes outside this map produce a warning + skip; we add them as we
# either land the programme module (Faz 5) or decide the programme isn't
# in scope.
PROGRAM_CODE_TO_PROGRAMME_ID: Final[dict[str, str]] = {
    "1501": "tubitak_1501",
    "1507": "tubitak_1507",
    "1505": "tubitak_1505",
    "1601": "tubitak_1601",
    "1512": "tubitak_1512",
    "1071": "tubitak_1071",
    "2244": "tubitak_2244",
}


# ── HTML parsing helpers ─────────────────────────────────────────────────

# Each call card is preceded by this marker; the first occurrence is part
# of the navigation header so we drop the head before splitting.
_BASLIK_DELIM: Final = '<div class="c-baslik">'

_TITLE_ANCHOR_RE: Final = re.compile(
    r'<a\s+href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>'
)
_PROGRAM_CODE_FROM_URL_RE: Final = re.compile(r"cagri-(?P<code>\d{4})-")
_PROGRAM_CODE_FROM_TITLE_RE: Final = re.compile(r"^\s*(?P<code>\d{4})\b")
_CYCLE_RE: Final = re.compile(
    r'<div class="c-kodu">#(?P<code>\d{4})\s+(?P<cycle>\d{4}-\d+)'
)
_TIME_DATETIME_RE: Final = re.compile(
    r'<time\s+datetime="(?P<dt>[^"]+)"', re.IGNORECASE
)
_FIELD_CONTENT_RE: Final = re.compile(
    r'<div class="c-icerik">.*?<a[^>]*>(?P<text>[^<]+)</a>'
)


def _strip_card(html_after_delim: str) -> str:
    """Truncate one segment at the next ``views-row`` boundary so we
    don't bleed the next card's HTML into this card's regexes."""

    boundary = html_after_delim.find('<div class="views-row">')
    if boundary >= 0:
        return html_after_delim[:boundary]
    return html_after_delim


def _parse_deadline_from_time_entries(card_html: str) -> date | None:
    """Two ``<time datetime>`` entries per card: start, then end. We use
    the **last** entry (end) as the deadline; falls back to None when
    fewer than two are present (malformed card)."""

    matches = _TIME_DATETIME_RE.findall(card_html)
    if not matches:
        return None
    return parse_deadline(matches[-1])


def _absolute(href: str) -> str:
    """Resolve a Drupal-relative href to an absolute URL on the funder."""

    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return f"{BASE_URL}/{href}"


def _parse_cards(html: str) -> list[dict[str, Any]]:
    """Extract one record per call card from the listing HTML."""

    segments = html.split(_BASLIK_DELIM)
    # Index 0 is the header / navigation; real cards start at index 1.
    cards: list[dict[str, Any]] = []
    for segment in segments[1 : 1 + MAX_CARDS_PER_LISTING]:
        card_html = _strip_card(segment)

        anchor = _TITLE_ANCHOR_RE.search(card_html)
        if not anchor:
            continue
        href = anchor.group("href").strip()
        title = anchor.group("title").strip()

        # Program code: prefer URL slug (most reliable), fall back to
        # the title's leading 4-digit number.
        code_match = _PROGRAM_CODE_FROM_URL_RE.search(href)
        if code_match is None:
            code_match = _PROGRAM_CODE_FROM_TITLE_RE.search(title)
        if code_match is None:
            logger.debug("tubitak_card_missing_code", extra={"title": title})
            continue
        program_code = code_match.group("code")

        cycle_match = _CYCLE_RE.search(card_html)
        cycle = cycle_match.group("cycle") if cycle_match else None

        deadline = _parse_deadline_from_time_entries(card_html)
        all_dts = _TIME_DATETIME_RE.findall(card_html)
        opening_dt = all_dts[0] if all_dts else None

        cards.append(
            {
                "program_code": program_code,
                "cycle": cycle,
                "title": title,
                "detail_url": _absolute(href),
                "deadline_iso": _normalise_iso_to_date_string(all_dts[-1])
                if all_dts
                else None,
                "opening_iso": _normalise_iso_to_date_string(opening_dt)
                if opening_dt
                else None,
                "deadline": deadline,
            }
        )
    return cards


def _normalise_iso_to_date_string(value: str | None) -> str | None:
    """Trim ``2026-06-09T20:59:59Z`` → ``2026-06-09``; pass through bad
    values unchanged so the caller can preserve diagnostic context."""

    if not value:
        return None
    return value.split("T", 1)[0]


# ── Scraper ──────────────────────────────────────────────────────────────


@register_scraper
class TUBITAKScraper(BaseScraper):
    """TÜBİTAK national support programmes scraper.

    V1 yields one :class:`NormalizedCall` per card on the open-calls
    listing whose program code is in :data:`PROGRAM_CODE_TO_PROGRAMME_ID`.
    Unknown codes log at INFO and the corresponding card is skipped —
    this is intentional: thematic calls (Yeşil Dönüşüm 1831/1832/1833,
    SAYEM, …) need their own programme metadata before we can persist
    them.
    """

    source = "tubitak"
    name = "TÜBİTAK"
    default_programme_id = None  # Multi-programme; resolved per card.

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        listing_url: str = LISTING_URL,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._listing_url = listing_url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def discover(self) -> AsyncIterator[dict[str, Any]]:
        """Fetch the public listing once and yield one record per call card."""

        client = await self._get_client()
        resp = await client.get(self._listing_url)
        resp.raise_for_status()
        cards = _parse_cards(resp.text)
        skipped: dict[str, int] = {}
        emitted = 0
        for card in cards:
            program_code = card["program_code"]
            if program_code not in PROGRAM_CODE_TO_PROGRAMME_ID:
                skipped[program_code] = skipped.get(program_code, 0) + 1
                continue
            yield {
                "external_id": _build_external_id(card),
                "card": card,
            }
            emitted += 1
        if skipped:
            logger.info(
                "tubitak_discover_skipped_unknown_codes",
                extra={"skipped_counts": skipped},
            )
        logger.info(
            "tubitak_discover_done",
            extra={"cards_found": len(cards), "emitted": emitted},
        )

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        card: dict[str, Any] = raw["card"]
        program_code = card["program_code"]
        programme_id = PROGRAM_CODE_TO_PROGRAMME_ID[program_code]
        detail_url = card["detail_url"]

        # Per docs/programs/tubitak.md the headline parameters per code:
        defaults = _DEFAULTS_BY_CODE.get(program_code, {})

        return NormalizedCall(
            source="tubitak",
            external_id=raw["external_id"],
            programme_id=programme_id,
            agency_id=programme_id,
            title=card["title"],
            scope_summary=None,  # Needs detail-page enrichment (V2).
            call_text=None,
            language="tr",
            call_url=detail_url,
            source_url_canonical=canonicalize_url(detail_url),
            opening_at=parse_deadline(card.get("opening_iso") or ""),
            deadline=card.get("deadline"),
            funding_rate_pct=defaults.get("funding_rate_pct"),
            trl_min=defaults.get("trl_min"),
            trl_max=defaults.get("trl_max"),
            topic_keywords=defaults.get("topic_keywords", []),
            sectors=[],
            geo_scope=["tr"],
            eligibility_tags=defaults.get("eligibility_tags", []),
            eligibility_summary=defaults.get("eligibility_summary", {}),
            partner_consortium_required=defaults.get(
                "partner_consortium_required"
            ),
            raw_metadata={
                "program_code": program_code,
                "cycle": card.get("cycle"),
                "deadline_iso_raw": card.get("deadline_iso"),
                "opening_iso_raw": card.get("opening_iso"),
            },
        )


def _build_external_id(card: dict[str, Any]) -> str:
    """``{code}-{cycle}`` when the card carries a cycle, else
    ``{code}-{deadline_iso}``. Keeps yearly re-runs from collapsing two
    distinct cycles onto the same row."""

    code = card["program_code"]
    cycle = card.get("cycle")
    if cycle:
        return f"{code}-{cycle}"
    deadline_iso = card.get("deadline_iso")
    return f"{code}-{deadline_iso}" if deadline_iso else code


# ── Programme-code defaults ──────────────────────────────────────────────
#
# Hard-coded metadata per code, used until V2 enriches from the detail
# page / PDF guideline. Numbers come from docs/programs/tubitak.md.

_DEFAULTS_BY_CODE: Final[dict[str, dict[str, Any]]] = {
    "1501": {
        "funding_rate_pct": 75,
        "trl_min": 2,
        "trl_max": 7,
        "topic_keywords": ["sanayi", "ar-ge", "endüstriyel"],
        "eligibility_tags": ["sme", "large_corp"],
        "eligibility_summary": {
            "countries": ["TR"],
            "entity_types": ["sermaye_sirketi"],
            "consortium_required": False,
            "max_projects_per_period": 2,
        },
        "partner_consortium_required": False,
    },
    "1507": {
        "funding_rate_pct": 75,
        "trl_min": 2,
        "trl_max": 6,
        "topic_keywords": ["kobi", "ar-ge", "başlangıç"],
        "eligibility_tags": ["sme"],
        "eligibility_summary": {
            "countries": ["TR"],
            "entity_types": ["sermaye_sirketi"],
            "sme_status_required": True,
            "consortium_required": False,
            "lifetime_proposal_cap": 5,
        },
        "partner_consortium_required": False,
    },
    "1505": {
        "funding_rate_pct": 70,  # Average; varies KOBİ vs büyük.
        "trl_min": 3,
        "trl_max": 7,
        "topic_keywords": ["üniversite-sanayi", "işbirliği"],
        "eligibility_tags": ["sme", "large_corp", "university"],
        "eligibility_summary": {
            "countries": ["TR"],
            "partnership_required": True,
            "consortium_required": True,
        },
        "partner_consortium_required": True,
    },
    "1601": {
        "funding_rate_pct": 100,
        "topic_keywords": ["yenilik", "girişimcilik", "kapasite"],
        "eligibility_tags": ["sme", "large_corp", "university", "ngo"],
        "eligibility_summary": {
            "countries": ["TR"],
            "consortium_required": False,
        },
        "partner_consortium_required": False,
    },
    "1512": {
        "trl_min": 2,
        "trl_max": 4,
        "topic_keywords": ["bigg", "girişimci", "startup"],
        "eligibility_tags": ["individual"],
        "eligibility_summary": {
            "countries": ["TR"],
            "individual_eligible": True,
            "consortium_required": False,
        },
        "partner_consortium_required": False,
    },
    "1071": {
        "topic_keywords": ["uluslararası", "ardeb", "araştırma"],
        "eligibility_tags": ["university", "research_org"],
        "eligibility_summary": {
            "countries": ["TR"],
            "international_collaboration_required": True,
        },
        "partner_consortium_required": True,
    },
    "2244": {
        "topic_keywords": ["sanayi doktora", "bideb", "doktora"],
        "eligibility_tags": ["university", "large_corp", "sme"],
        "eligibility_summary": {
            "countries": ["TR"],
            "partnership_required": True,
        },
        "partner_consortium_required": True,
    },
}


__all__ = [
    "PROGRAM_CODE_TO_PROGRAMME_ID",
    "TUBITAKScraper",
]
