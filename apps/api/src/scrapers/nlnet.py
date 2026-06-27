"""NLnet Foundation scraper.

NLnet's funding model is unusual among the catalogues we cover: instead
of publishing a rolling list of distinct calls, it runs **three active
funds** (NGI0 Commons Fund, NGI Taler, NGI Fediversity) on the **same
fixed 2-month cycle** — applications close on the 1st of every even
month at 12:00 CEST (1 Feb / 1 Apr / 1 Jun / 1 Aug / 1 Oct / 1 Dec).

Per docs/programs/nlnet.md (NGI0 Core and NGI0 Entrust are closed as of
2026-Q1) we surface exactly the three currently-open funds.

V1 (this module) emits one :class:`NormalizedCall` per fund per next
upcoming deadline — deterministic, no network needed. The Atom feed
(``https://nlnet.nl/feed.atom``) cross-check that confirms the fund is
still accepting submissions lands in V2 (Faz 1.7 runner integration).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from src.scrapers import register_scraper
from src.scrapers.base import BaseScraper, EligibilityTag, NormalizedCall

# The three currently-open NLnet funds. Each one funds the same kind of
# work (open-source internet technology, "clear European dimension") at
# the same budget band; what differs is the thematic focus.
_ACTIVE_FUNDS: tuple[dict[str, Any], ...] = (
    {
        "agency_id": "nlnet_ngi0_commons",
        "name": "NGI0 Commons Fund",
        "fund_url": "https://nlnet.nl/commonsfund/",
        "scope_summary": (
            "Funding for free and open-source projects that strengthen "
            "Europe's digital commons — software, hardware, standards, "
            "and educational materials released under recognised "
            "open-source licences. First applications up to €50,000; "
            "per-proposal cap €150,000; lifetime cap per third party €500,000."
        ),
        "topic_keywords": [
            "open source", "internet technology", "digital commons",
            "free software", "open standards",
        ],
        "budget_per_project_min_eur": 5_000.0,
        "budget_per_project_max_eur": 50_000.0,
    },
    {
        "agency_id": "nlnet_ngi_taler",
        "name": "NGI Taler",
        "fund_url": "https://nlnet.nl/taler/",
        "scope_summary": (
            "GNU Taler privacy-preserving payments ecosystem — funding "
            "for developers, integrators and researchers building on or "
            "around the Taler payment protocol."
        ),
        "topic_keywords": [
            "privacy", "payments", "gnu taler", "digital currency",
        ],
        "budget_per_project_min_eur": 5_000.0,
        "budget_per_project_max_eur": 50_000.0,
    },
    {
        "agency_id": "nlnet_ngi_fediversity",
        "name": "NGI Fediversity",
        "fund_url": "https://nlnet.nl/fediversity/",
        "scope_summary": (
            "Fediverse and decentralised social protocols — ActivityPub, "
            "Matrix, and related federated technologies. Funds developers, "
            "instance operators, and protocol researchers."
        ),
        "topic_keywords": [
            "fediverse", "activitypub", "matrix", "federated",
            "decentralised social",
        ],
        "budget_per_project_min_eur": 5_000.0,
        "budget_per_project_max_eur": 50_000.0,
    },
)


# Eligible-applicant tags are the same across all three funds. Pulled
# from /commonsfund/guideforapplicants/ — explicitly inclusive of
# individuals, SMEs, universities, NGOs, and community groups.
_NLNET_ELIGIBILITY_TAGS: list[EligibilityTag] = [
    "individual",
    "sme",
    "university",
    "ngo",
    "research_org",
]


# All NLnet calls are open EU-wide; the funder requires a "clear European
# dimension" rather than EU-only applicants. We tag with EU + Associated
# so the IdeaMatcher (Faz 2) doesn't filter out Turkish applicants.
_NLNET_GEO_SCOPE: list[str] = ["eu27", "assoc", "global"]


def _compute_next_deadline(today: date | None = None) -> date:
    """Return the next 1st-of-even-month on or after ``today``.

    NLnet's published cycle is 2-monthly with deadlines at 12:00 CEST on
    the 1st of February, April, June, August, October, and December.
    """

    today = today or date.today()
    for month in (2, 4, 6, 8, 10, 12):
        candidate = date(today.year, month, 1)
        if candidate >= today:
            return candidate
    # All this year's deadlines have passed → first window next year.
    return date(today.year + 1, 2, 1)


@register_scraper
class NLnetScraper(BaseScraper):
    """Yields one open call per active fund per upcoming deadline.

    NLnet's open-call model means we publish a fresh ``external_id`` for
    each cycle: ``nlnet_ngi0_commons-2026-06-01`` rather than a stable
    URL. Callers re-running the scraper after the deadline produces a
    *new* call row for the next cycle automatically.
    """

    source = "nlnet"
    name = "NLnet Foundation"
    # NLnet currently maps onto the "cascade_funding" programme bucket
    # in the registry. Faz 5 splits NLnet into its own ``nlnet`` programme
    # module; the scraper will switch ``default_programme_id`` there too.
    default_programme_id = "cascade_funding"

    def __init__(self, *, today: date | None = None) -> None:
        """``today`` lets tests pin the cycle calculation deterministically."""

        self._today = today

    async def discover(self) -> AsyncIterator[dict[str, Any]]:
        deadline = _compute_next_deadline(self._today)
        deadline_iso = deadline.isoformat()
        for fund in _ACTIVE_FUNDS:
            yield {
                **fund,
                "deadline": deadline_iso,
            }

    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        deadline = date.fromisoformat(raw["deadline"])
        external_id = f"{raw['agency_id']}-{raw['deadline']}"
        # ``%-d`` (no leading zero) isn't portable on Windows; build the
        # day manually so CI on win + tests on linux agree.
        title = (
            f"{raw['name']} - Call closing "
            f"{deadline.day} {deadline.strftime('%B %Y')}"
        )

        return NormalizedCall(
            source="nlnet",
            external_id=external_id,
            programme_id=self.default_programme_id or "cascade_funding",
            agency_id=raw["agency_id"],
            title=title,
            scope_summary=raw["scope_summary"],
            call_text=raw["scope_summary"],
            language="en",
            call_url=raw["fund_url"],
            source_url_canonical=raw["fund_url"],
            application_form_url="https://nlnet.nl/propose/",
            deadline=deadline,
            opening_at=None,  # Rolling — no published opening date per cycle
            budget_per_project_min_eur=raw["budget_per_project_min_eur"],
            budget_per_project_max_eur=raw["budget_per_project_max_eur"],
            funding_rate_pct=100,  # NLnet grants are full-cost (no co-funding)
            trl_min=None,
            trl_max=None,
            topic_keywords=list(raw["topic_keywords"]),
            sectors=["J62"],  # Information service activities (NACE Rev.2)
            geo_scope=list(_NLNET_GEO_SCOPE),
            eligibility_tags=list(_NLNET_ELIGIBILITY_TAGS),
            eligibility_summary={
                "european_dimension_required": True,
                "open_source_licence_required": True,
                "individual_eligible": True,
                "sme_eligible": True,
                "consortium_required": False,
            },
            partner_consortium_required=False,
            raw_metadata={
                "fund_name": raw["name"],
                "cycle": "2-monthly",
                "deadline_time": "12:00 CEST",
                "evaluation_criteria_weights": {
                    "technical_excellence": 0.30,
                    "relevance_impact": 0.40,
                    "cost_effectiveness": 0.30,
                },
                "pass_threshold": "5.0/7.0",
            },
        )


__all__ = ["NLnetScraper"]
