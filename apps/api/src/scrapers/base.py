"""``BaseScraper`` — plugin interface for grant call discovery.

Each upstream catalogue (EU F&T Portal, NLnet, Cascade Funding, TÜBİTAK,
KOSGEB, Eurostars, …) is implemented as one concrete subclass under
``src/scrapers/<source>.py``. Scrapers don't touch the ``calls`` table
directly — they yield :class:`NormalizedCall` records, and the orchestrator
in :mod:`src.scrapers.runner` (Faz 1) persists with upsert + dedup.

Adding a new source = new module + one line in ``src/scrapers/__init__.py``
``SCRAPER_REGISTRY``. The same pattern as ``src/programs``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

# Keep in sync with the CHECK constraint on ``calls.source`` (migration v2).
# ``manual`` is reserved for operator-seeded rows that don't come from any
# scraper; it never appears in scraper output.
CallSource = Literal[
    "eu_ft_portal",
    "nlnet",
    "cascade",
    "tubitak",
    "kosgeb",
    "eurostars",
    "schumann",
    "manual",
]
CallLifecycleStatus = Literal["open", "closing_soon", "closed", "draft"]
EligibilityTag = Literal[
    "individual",
    "sme",
    "university",
    "research_org",
    "large_corp",
    "ngo",
    "consortium_required",
    "lead_must_be_sme",
]


class NormalizedCall(BaseModel):
    """Scraper output — the shape that ``runner.persist()`` accepts.

    Every scraper builds one of these per upstream call. Fields the upstream
    doesn't provide stay ``None`` / empty; downstream agents (CallAnalyst,
    EligibilityChecker) can fill them later from the PDF guideline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── Identity ──────────────────────────────────────────────────────
    source: CallSource
    external_id: str
    """Source-specific unique id. Combined with ``source`` for upsert."""
    programme_id: str
    """References ``programmes.id``; e.g. ``horizon_eu_ria``."""
    agency_id: str | None = None
    """Sub-programme handle when the funder runs many tracks under one
    programme — e.g. ``nlnet_ngi0_core`` vs ``nlnet_ngi0_entrust``,
    ``tubitak_1501`` vs ``tubitak_1507``. Free-form text; the call browser
    uses it for granular faceted filtering."""

    # ── Core content ──────────────────────────────────────────────────
    title: str
    call_text: str | None = None
    """Full call narrative if we can fetch it (RIA/IA work programme excerpt,
    NLnet theme page body, etc.). Used for embedding + RAG retrieval."""
    scope_summary: str | None = None
    """1-3 sentence funder-provided abstract; falls back to first paragraph
    of ``call_text`` when missing."""
    language: Literal["tr", "en"] = "en"
    call_url: str
    """Canonical funder URL — the user-facing 'apply here' link."""
    source_url_canonical: str | None = None
    """Cleaned, canonical URL (no UTM, sorted query, trailing slash policy).
    Used for cross-source dedup; defaults to ``call_url`` after running
    :func:`src.scrapers.normalization.canonicalize_url`."""
    call_pdf_url: str | None = None
    """Funder-published PDF guideline / work programme excerpt. Triggers
    :func:`src.tasks.ingest_guidelines.ingest_call_guideline` on persist."""
    application_form_url: str | None = None
    work_programme_pdf_url: str | None = None

    # ── Dates ─────────────────────────────────────────────────────────
    opening_at: date | None = None
    deadline: date | None = None
    deadlines_extra: dict[str, str] = Field(default_factory=dict)
    """Stage-2 / interim deadlines keyed by stage name (e.g.
    ``{"stage_1": "2026-09-15", "stage_2": "2027-03-01"}``)."""

    # ── Budget (EUR) ──────────────────────────────────────────────────
    budget_total_eur: float | None = None
    budget_per_project_min_eur: float | None = None
    budget_per_project_max_eur: float | None = None
    funding_rate_pct: int | None = None
    """% of eligible costs covered by the grant (TÜBİTAK 1501 ≈ 75, HE
    RIA ≈ 100). When the upstream uses a TL/USD figure, the scraper
    converts via :func:`src.scrapers.normalization.to_eur`."""

    # ── Tech band ─────────────────────────────────────────────────────
    trl_min: int | None = None
    trl_max: int | None = None

    # ── Taxonomy ──────────────────────────────────────────────────────
    topic_keywords: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    """NACE Rev.2 codes (e.g. ``J62``) and/or human-readable sector tags
    (e.g. ``"renewable energy"``). The normalizer ``map_to_nace`` resolves
    free-form sector strings to NACE codes when possible."""
    geo_scope: list[str] = Field(default_factory=list)
    """ISO-3166 country codes and/or region tags (``eu27``, ``assoc``,
    ``global``). ``["tr"]`` for TÜBİTAK; ``["eu27", "assoc"]`` for HE."""
    eligibility_tags: list[EligibilityTag] = Field(default_factory=list)
    eligibility_summary: dict[str, Any] = Field(default_factory=dict)
    """Structured eligibility data the EligibilityChecker reads at match
    time: ``{"countries": [...], "entity_types": [...],
    "min_partners": 3, "consortium_required": true, ...}``."""
    partner_consortium_required: bool | None = None
    """Convenience flag the call browser surfaces directly. Mirrors the
    ``consortium_required`` element of ``eligibility_summary`` when set."""

    # ── Provenance ────────────────────────────────────────────────────
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    """Source-specific blob — topic code, work programme id, EUREKA cluster,
    NACE filters, anything we may need later but isn't on a typed field."""
    raw_html: str | None = None
    """Verbatim HTML snippet captured at scrape time. Useful when the
    upstream page changes shape and we need to back-fill old records."""
    scraped_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """When this NormalizedCall was constructed. ``runner.persist()``
    writes it to ``calls.last_seen_at`` too."""


class ScraperRunResult(BaseModel):
    """Summary of one scraper invocation. Written to ``scraper_runs``."""

    model_config = ConfigDict(frozen=True)

    source: CallSource
    started_at: datetime
    finished_at: datetime
    calls_discovered: int = 0
    calls_persisted: int = 0
    """New rows in ``calls``."""
    calls_updated: int = 0
    """Existing rows refreshed (matched on ``(source, external_id)``)."""
    calls_failed: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    """Each: ``{"external_id": "...", "stage": "fetch_detail|normalize|persist", "error": "..."}``."""

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class BaseScraper(ABC):
    """One concrete subclass per upstream catalogue.

    Lifecycle, by ``runner.run_scraper(source)``:

    1. ``async for raw in scraper.discover()`` — pagination + index parsing.
       The scraper yields lightweight dicts (or external_ids) — enough to
       skip already-seen calls without paying for detail fetches.
    2. ``raw = await scraper.fetch_call_detail(external_id)`` — per-call
       deep fetch (full text, PDF link, deadline, eligibility summary).
       Default implementation returns the discover() output as-is; sources
       with cheap index endpoints (EU F&T API) override this; sources with
       two-step pages (TÜBİTAK destek list + detail page) implement both.
    3. ``call = await scraper.normalize(raw)`` — to :class:`NormalizedCall`.
       This is where currency conversion, NACE mapping, TRL extraction,
       eligibility parsing happen — typically by calling helpers in
       :mod:`src.scrapers.normalization`.
    4. ``await runner.persist(call)`` — upsert + dedup + guideline ingest.

    Subclasses must be safe to instantiate without I/O (the registry calls
    them lazily). Any HTTP client / authentication setup lives in
    ``async def __aenter__`` if needed.
    """

    source: ClassVar[CallSource]
    name: ClassVar[str]
    """Human-readable name for logs / admin dashboard."""
    default_programme_id: ClassVar[str | None] = None
    """When the scraper exclusively serves one programme (e.g. Eurostars),
    this is the ``programmes.id`` value. ``None`` for sources that span
    multiple programmes (EU F&T Portal → RIA/IA/CSA/…)."""

    @abstractmethod
    def discover(self) -> AsyncIterator[dict[str, Any]]:
        """Yield one lightweight record per discovered open call.

        Records MUST contain at least ``external_id``. Other fields are
        optional — they let downstream skip already-seen calls without
        paying for the detail fetch.
        """

    async def fetch_call_detail(
        self, external_id: str, *, discover_payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Deep-fetch one call. Default: pass through the discover payload.

        Override when the source uses a list-page + detail-page pattern.
        ``discover_payload`` is the dict yielded from :meth:`discover` so
        subclasses can reuse the title / URL it already contains without a
        second request.
        """

        if discover_payload is not None:
            return dict(discover_payload)
        return {"external_id": external_id}

    @abstractmethod
    async def normalize(self, raw: dict[str, Any]) -> NormalizedCall:
        """Build a :class:`NormalizedCall` from a detail payload.

        Programme-id resolution lives here: when ``default_programme_id``
        is ``None``, the scraper inspects ``raw`` (topic code, theme name)
        and chooses one entry from :data:`src.programs.REGISTRY`. Calls
        that map to no registered programme are dropped — the runner logs
        a warning rather than failing the whole run.
        """


__all__ = [
    "BaseScraper",
    "CallLifecycleStatus",
    "CallSource",
    "EligibilityTag",
    "NormalizedCall",
    "ScraperRunResult",
]
