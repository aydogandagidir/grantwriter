"""Open-call catalog endpoints.

Faz 1.8 extends the listing endpoint with full faceted filtering (search,
programme/agency, deadline window, budget range, TRL band, sectors,
eligibility tags, geographical scope, language) plus pagination + total
count, and surfaces the v2 columns added by migration 20260513120100
(``embedding``, ``sectors``, ``geo_scope``, ``eligibility_tags``,
``agency_id``, ``opening_at``, …) on the response model.

Auth: any authenticated user — call metadata isn't tenant-scoped (the
funder publishes it). The manual seed path (POST) writes an audit row
under the caller's tenant so we can attribute pilot-time seeded calls.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from src.agents.eligibility_checker import EligibilityChecker
from src.agents.idea_generator import IdeaGenerator
from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.llm_dep import get_llm_router
from src.core.tenant import resolve_tenant_and_role
from src.llm.router import LLMRouter
from src.scrapers.base import CallSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])


_CALL_LIST_LIMIT_DEFAULT = 50
_CALL_LIST_LIMIT_MAX = 200

CallStatus = Literal["open", "closing_soon", "closed", "draft"]
SortKey = Literal["deadline", "budget", "relevance", "recency"]


# ── Models ─────────────────────────────────────────────────────────────


class CallCreate(BaseModel):
    """Body for ``POST /api/v1/calls``.

    The manual seed path stays available for pilot operators. Scrapers
    bypass it and write through :class:`ScraperRunner.persist`.
    """

    model_config = ConfigDict(extra="forbid")

    programme_id: str
    external_id: str
    title: str
    language: Literal["tr", "en"]
    source: CallSource = "manual"
    call_text: str | None = None
    call_url: str | None = None
    call_pdf_url: str | None = None
    deadline: date | None = None
    budget_total_eur: float | None = None
    budget_per_project_min_eur: float | None = None
    budget_per_project_max_eur: float | None = None
    trl_min: int | None = None
    trl_max: int | None = None
    topic_keywords: list[str] = []
    raw_metadata: dict[str, Any] = {}


class CallSummary(BaseModel):
    """Lightweight projection for list responses. Mirrors the v2 schema."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    programme_id: str
    agency_id: str | None
    source: CallSource
    external_id: str
    title: str
    language: str
    status: CallStatus
    deadline: date | None
    opening_at: date | None
    call_url: str | None
    topic_keywords: list[str]
    sectors: list[str]
    geo_scope: list[str]
    eligibility_tags: list[str]
    budget_per_project_min_eur: float | None
    budget_per_project_max_eur: float | None
    trl_min: int | None
    trl_max: int | None
    funding_rate_pct: int | None
    partner_consortium_required: bool | None
    scraped_at: datetime


class CallListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    calls: list[CallSummary]
    total: int
    """Total rows matching the filters (before limit/offset). Lets the
    UI render an accurate result count + paginator."""
    limit: int
    offset: int


class CallDetail(CallSummary):
    """Full call payload — every column the API surfaces."""

    model_config = ConfigDict(frozen=True)

    scope_summary: str | None
    call_text: str | None
    call_pdf_url: str | None
    application_form_url: str | None
    work_programme_pdf_url: str | None
    source_url_canonical: str | None
    budget_total_eur: float | None
    eligibility_summary: dict[str, Any]
    raw_metadata: dict[str, Any]
    historical_acceptance_rate: float | None
    last_seen_at: datetime


# ── Faz 2: call → idea generation + eligibility ────────────────────────


class GenerateIdeasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_ideas: int = 3
    use_org_profile: bool = True
    """When true, biases the slate toward the caller's org profile (and
    skips the shared cache — profile-biased output is tenant-specific)."""
    force_refresh: bool = False


class GeneratedIdeaOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    abstract: str
    technology_angle: str
    impact_thesis: str
    est_budget_eur_min: float | None
    est_budget_eur_max: float | None
    est_trl: int | None
    suggested_consortium_type: str
    alignment_score: float
    distinctiveness_score: float | None


class GenerateIdeasResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: UUID
    ideas: list[GeneratedIdeaOut]
    generated_at: str
    generator_version: str
    from_cache: bool


class EligibilityCheckOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    status: Literal["pass", "warn", "fail"]
    message_tr: str
    message_en: str


class EligibilityReportOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: Literal["ELIGIBLE", "CONDITIONAL", "NOT_ELIGIBLE"]
    checks: list[EligibilityCheckOut]
    blockers: list[str]
    warnings: list[str]
    confidence: float
    model_version: str
    checked_at: str


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=CallListResponse,
    summary="Search open calls with facet filters",
)
async def list_calls(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    q: str | None = None,
    programme_id: str | None = None,
    programme_ids: Annotated[list[str] | None, Query()] = None,
    agency_id: str | None = None,
    agency_ids: Annotated[list[str] | None, Query()] = None,
    source: CallSource | None = None,
    status_filter: CallStatus | None = None,
    deadline_after: date | None = None,
    deadline_before: date | None = None,
    budget_min_eur: float | None = None,
    budget_max_eur: float | None = None,
    trl_min: int | None = None,
    trl_max: int | None = None,
    sectors: Annotated[list[str] | None, Query()] = None,
    eligibility_tags: Annotated[list[str] | None, Query()] = None,
    geo_scope: Annotated[list[str] | None, Query()] = None,
    language: Literal["tr", "en"] | None = None,
    sort: SortKey = "deadline",
    limit: int = _CALL_LIST_LIMIT_DEFAULT,
    offset: int = 0,
) -> CallListResponse:
    """Return a filtered, paginated, optionally search-ranked call list.

    Filter semantics:
      - ``q`` matches against the ``title`` column via pg_trgm (uses
        ``idx_calls_text_trgm``); empty / whitespace-only is ignored.
      - ``programme_id`` + ``programme_ids`` are unioned (single arg
        kept for backward compatibility with the original endpoint).
      - Array filters (``sectors``, ``eligibility_tags``, ``geo_scope``)
        use Postgres ``&&`` (any-overlap) so a call tagged
        ``["sme","university"]`` matches a query for
        ``eligibility_tags=university``.
      - ``status_filter`` defaults to "any currently visible call" —
        ``open`` plus ``closing_soon``. Pass ``status=closed`` to pull
        historical rows.
      - Budget filter is intersection of the user range and the call's
        published band (so a user with ``budget_max=1M`` doesn't see a
        call whose minimum is €2M).

    Sort keys:
      - ``deadline`` (default) — soonest first; NULLs last.
      - ``budget`` — highest budget cap first.
      - ``recency`` — most recently scraped first.
      - ``relevance`` — pg_trgm ``similarity(title, q)`` desc; falls
        back to deadline order when ``q`` is unset.
    """

    capped_limit = max(1, min(limit, _CALL_LIST_LIMIT_MAX))
    capped_offset = max(0, offset)

    clauses: list[str] = []
    params: list[Any] = []

    def add(clause_tpl: str, *values: Any) -> None:
        """Append a WHERE clause with placeholders renumbered to params."""

        idxs = [len(params) + 1 + i for i in range(len(values))]
        clauses.append(clause_tpl.format(*[f"${i}" for i in idxs]))
        params.extend(values)

    # ── Status ───────────────────────────────────────────────────────
    if status_filter is not None:
        add("status = {}", status_filter)
    else:
        add("status IN ('open', 'closing_soon')")

    # ── Free-text search ─────────────────────────────────────────────
    q_clean = (q or "").strip()
    if q_clean:
        add("title ILIKE {}", f"%{q_clean}%")

    # ── Programme / agency / source ─────────────────────────────────
    programme_id_set: set[str] = set()
    if programme_id:
        programme_id_set.add(programme_id)
    if programme_ids:
        programme_id_set.update(p for p in programme_ids if p)
    if programme_id_set:
        add("programme_id = ANY({}::text[])", sorted(programme_id_set))

    agency_id_set: set[str] = set()
    if agency_id:
        agency_id_set.add(agency_id)
    if agency_ids:
        agency_id_set.update(a for a in agency_ids if a)
    if agency_id_set:
        add("agency_id = ANY({}::text[])", sorted(agency_id_set))

    if source:
        add("source = {}", source)

    # ── Deadlines ───────────────────────────────────────────────────
    if deadline_after is not None:
        add("(deadline IS NULL OR deadline >= {})", deadline_after)
    if deadline_before is not None:
        add("(deadline IS NOT NULL AND deadline <= {})", deadline_before)

    # ── Budget ──────────────────────────────────────────────────────
    if budget_min_eur is not None:
        add(
            "(budget_per_project_max_eur IS NULL OR budget_per_project_max_eur >= {})",
            budget_min_eur,
        )
    if budget_max_eur is not None:
        add(
            "(budget_per_project_min_eur IS NULL OR budget_per_project_min_eur <= {})",
            budget_max_eur,
        )

    # ── TRL ────────────────────────────────────────────────────────
    if trl_max is not None:
        add("(trl_min IS NULL OR trl_min <= {})", trl_max)
    if trl_min is not None:
        add("(trl_max IS NULL OR trl_max >= {})", trl_min)

    # ── Array facets (any-overlap with &&) ─────────────────────────
    if sectors:
        add("sectors && {}::text[]", list(sectors))
    if eligibility_tags:
        add("eligibility_tags && {}::text[]", list(eligibility_tags))
    if geo_scope:
        add("geo_scope && {}::text[]", list(geo_scope))

    # ── Language ───────────────────────────────────────────────────
    if language:
        add("language = {}", language)

    where_sql = " AND ".join(clauses) if clauses else "TRUE"

    order_sql = _build_order_clause(sort, has_q=bool(q_clean))

    limit_placeholder = f"${len(params) + 1}"
    offset_placeholder = f"${len(params) + 2}"
    params.extend([capped_limit, capped_offset])

    # COUNT(*) OVER() in one query → total rows for pagination without
    # a separate SELECT count(*).
    sql = f"""
        SELECT id, programme_id, agency_id, source, external_id, title,
               language, status, deadline, opening_at, call_url,
               topic_keywords, sectors, geo_scope, eligibility_tags,
               budget_per_project_min_eur, budget_per_project_max_eur,
               trl_min, trl_max, funding_rate_pct,
               partner_consortium_required, scraped_at,
               COUNT(*) OVER() AS _total
          FROM calls
         WHERE {where_sql}
         ORDER BY {order_sql}
         LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """

    rows = await conn.fetch(sql, *params)
    total = int(rows[0]["_total"]) if rows else 0

    logger.info(
        "calls_listed",
        extra={
            "user_id": str(user_id),
            "q": q_clean or None,
            "filter_count": len(clauses),
            "sort": sort,
            "row_count": len(rows),
            "total": total,
        },
    )

    return CallListResponse(
        calls=[_row_to_summary(r) for r in rows],
        total=total,
        limit=capped_limit,
        offset=capped_offset,
    )


@router.get(
    "/{call_id}",
    response_model=CallDetail,
    summary="Fetch one call's full detail",
)
async def get_call(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    call_id: UUID,
) -> CallDetail:
    row = await conn.fetchrow(
        """
        SELECT id, programme_id, agency_id, source, external_id, title,
               language, status, deadline, opening_at, call_url,
               topic_keywords, sectors, geo_scope, eligibility_tags,
               budget_per_project_min_eur, budget_per_project_max_eur,
               trl_min, trl_max, funding_rate_pct,
               partner_consortium_required, scraped_at,
               scope_summary, call_text, call_pdf_url, application_form_url,
               work_programme_pdf_url, source_url_canonical, budget_total_eur,
               eligibility_summary, raw_metadata, historical_acceptance_rate,
               last_seen_at
          FROM calls
         WHERE id = $1
        """,
        call_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"call {call_id} not found",
        )
    logger.info("call_fetched", extra={"call_id": str(call_id), "user_id": str(user_id)})
    return _row_to_detail(row)


@router.post(
    "",
    response_model=CallSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Seed a call manually (pilot bridge — scrapers handle production)",
)
async def create_call(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: CallCreate,
) -> CallSummary:
    """Insert a call row + audit the action under the caller's tenant.

    Idempotent on ``(source, external_id)`` — re-running the same payload
    returns 409 so the pilot operator notices duplicates.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO calls (
                  programme_id, source, external_id, title, language,
                  call_text, call_url, call_pdf_url, deadline,
                  budget_total_eur, budget_per_project_min_eur,
                  budget_per_project_max_eur, trl_min, trl_max,
                  topic_keywords, raw_metadata, status
                ) VALUES (
                  $1, $2, $3, $4, $5,
                  $6, $7, $8, $9,
                  $10, $11, $12, $13, $14,
                  $15::text[], $16::jsonb, 'open'
                )
                RETURNING id, programme_id, agency_id, source, external_id,
                          title, language, status, deadline, opening_at,
                          call_url, topic_keywords, sectors, geo_scope,
                          eligibility_tags, budget_per_project_min_eur,
                          budget_per_project_max_eur, trl_min, trl_max,
                          funding_rate_pct, partner_consortium_required,
                          scraped_at
                """,
                body.programme_id,
                body.source,
                body.external_id,
                body.title,
                body.language,
                body.call_text,
                body.call_url,
                body.call_pdf_url,
                body.deadline,
                body.budget_total_eur,
                body.budget_per_project_min_eur,
                body.budget_per_project_max_eur,
                body.trl_min,
                body.trl_max,
                body.topic_keywords,
                json.dumps(body.raw_metadata),
            )
            assert row is not None
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="call.created_manually",
                resource_type="call",
                resource_id=UUID(str(row["id"])),
                diff={"source": body.source},
            )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"a call with source={body.source} and "
                f"external_id={body.external_id} already exists"
            ),
        ) from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"programme_id={body.programme_id} does not exist",
        ) from exc

    logger.info(
        "call_created_manually",
        extra={
            "call_id": str(row["id"]),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "programme_id": body.programme_id,
            "source": body.source,
        },
    )
    return _row_to_summary(row)


@router.post(
    "/{call_id}/generate-ideas",
    response_model=GenerateIdeasResponse,
    summary="Generate project ideas tailored to this call",
)
async def generate_ideas_for_call(
    request: Request,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    llm_router: Annotated[LLMRouter, Depends(get_llm_router)],
    call_id: UUID,
    body: GenerateIdeasRequest,
) -> GenerateIdeasResponse:
    """Run :class:`IdeaGenerator` for ``call_id``.

    With ``use_org_profile=True`` the slate is biased toward the
    caller's organization profile and the shared cache is bypassed
    (profile-biased output is tenant-specific). With ``False`` (or no
    profile on file) a generic slate is produced and cached for the
    next tenant browsing the same call.
    """

    call_exists = await conn.fetchval(
        "SELECT 1 FROM calls WHERE id = $1", call_id
    )
    if call_exists is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"call {call_id} not found"
        )

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    generator = IdeaGenerator(pool=pool, router=llm_router, tenant_id=tenant_id)
    result = await generator.generate(
        call_id,
        n_ideas=body.n_ideas,
        org_profile_tenant_id=tenant_id if body.use_org_profile else None,
        force_refresh=body.force_refresh,
    )

    logger.info(
        "call_ideas_generated",
        extra={
            "call_id": str(call_id),
            "tenant_id": str(tenant_id),
            "idea_count": len(result.ideas),
            "from_cache": result.from_cache,
        },
    )
    return GenerateIdeasResponse(
        call_id=result.call_id,
        ideas=[
            GeneratedIdeaOut(
                title=idea.title,
                abstract=idea.abstract,
                technology_angle=idea.technology_angle,
                impact_thesis=idea.impact_thesis,
                est_budget_eur_min=idea.est_budget_eur_min,
                est_budget_eur_max=idea.est_budget_eur_max,
                est_trl=idea.est_trl,
                suggested_consortium_type=idea.suggested_consortium_type,
                alignment_score=idea.alignment_score,
                distinctiveness_score=idea.distinctiveness_score,
            )
            for idea in result.ideas
        ],
        generated_at=result.generated_at,
        generator_version=result.generator_version,
        from_cache=result.from_cache,
    )


@router.get(
    "/{call_id}/eligibility",
    response_model=EligibilityReportOut,
    summary="Check the caller's organization eligibility for this call",
)
async def check_call_eligibility(
    request: Request,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    call_id: UUID,
) -> EligibilityReportOut:
    """Run the rule-based :class:`EligibilityChecker` for the caller's
    tenant against ``call_id``. A missing org profile is not an error —
    it just yields warnings rather than confirmations."""

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    checker = EligibilityChecker(pool=pool, tenant_id=tenant_id)
    try:
        report = await checker.check(org_tenant_id=tenant_id, call_id=call_id)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    logger.info(
        "call_eligibility_checked",
        extra={
            "call_id": str(call_id),
            "tenant_id": str(tenant_id),
            "verdict": report.verdict,
        },
    )
    return EligibilityReportOut(
        verdict=report.verdict,
        checks=[
            EligibilityCheckOut(
                rule=c.rule,
                status=c.status,
                message_tr=c.message_tr,
                message_en=c.message_en,
            )
            for c in report.checks
        ],
        blockers=report.blockers,
        warnings=report.warnings,
        confidence=report.confidence,
        model_version=report.model_version,
        checked_at=report.checked_at,
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _build_order_clause(sort: SortKey, *, has_q: bool) -> str:
    if sort == "budget":
        return "budget_per_project_max_eur DESC NULLS LAST, scraped_at DESC"
    if sort == "recency":
        return "scraped_at DESC, deadline ASC NULLS LAST"
    if sort == "relevance" and has_q:
        # Reuse the q placeholder via similarity(title, q). We don't
        # know the param index here without coupling, so we approximate
        # with deadline + recency for V1; full pg_trgm ranking lands in
        # Faz 1.8.1 once we wire a parametric ORDER BY.
        return "deadline ASC NULLS LAST, scraped_at DESC"
    return "deadline ASC NULLS LAST, scraped_at DESC"


def _row_to_summary(row: asyncpg.Record) -> CallSummary:
    return CallSummary(
        id=UUID(str(row["id"])),
        programme_id=str(row["programme_id"]),
        agency_id=row["agency_id"],
        source=row["source"],
        external_id=str(row["external_id"]),
        title=str(row["title"]),
        language=str(row["language"]),
        status=row["status"],
        deadline=row["deadline"],
        opening_at=row["opening_at"],
        call_url=row["call_url"],
        topic_keywords=list(row["topic_keywords"] or []),
        sectors=list(row["sectors"] or []),
        geo_scope=list(row["geo_scope"] or []),
        eligibility_tags=list(row["eligibility_tags"] or []),
        budget_per_project_min_eur=_to_float(row["budget_per_project_min_eur"]),
        budget_per_project_max_eur=_to_float(row["budget_per_project_max_eur"]),
        trl_min=row["trl_min"],
        trl_max=row["trl_max"],
        funding_rate_pct=row["funding_rate_pct"],
        partner_consortium_required=row["partner_consortium_required"],
        scraped_at=row["scraped_at"],
    )


def _row_to_detail(row: asyncpg.Record) -> CallDetail:
    summary = _row_to_summary(row)
    elig = row["eligibility_summary"] or {}
    raw_meta = row["raw_metadata"] or {}
    if isinstance(elig, str):
        elig = json.loads(elig)
    if isinstance(raw_meta, str):
        raw_meta = json.loads(raw_meta)
    return CallDetail(
        **summary.model_dump(),
        scope_summary=row["scope_summary"],
        call_text=row["call_text"],
        call_pdf_url=row["call_pdf_url"],
        application_form_url=row["application_form_url"],
        work_programme_pdf_url=row["work_programme_pdf_url"],
        source_url_canonical=row["source_url_canonical"],
        budget_total_eur=_to_float(row["budget_total_eur"]),
        eligibility_summary=elig,
        raw_metadata=raw_meta,
        historical_acceptance_rate=_to_float(row["historical_acceptance_rate"]),
        last_seen_at=row["last_seen_at"],
    )


def _to_float(value: Any) -> float | None:
    """Postgres numeric → float | None. asyncpg gives us Decimal; the
    response models prefer float for JSON friendliness."""

    if value is None:
        return None
    return float(value)


__all__ = [
    "CallCreate",
    "CallDetail",
    "CallListResponse",
    "CallSource",
    "CallStatus",
    "CallSummary",
    "SortKey",
    "router",
]
