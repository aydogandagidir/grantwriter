"""Project-idea endpoints — the 'I have an idea' half of bidirectional matching.

Flow:
  1. ``POST /api/v1/ideas`` — user submits a project idea. We embed
     ``title + abstract + technology_angle`` synchronously (idea volume
     is low, ~1/user/session) and store the row with its vector.
  2. ``POST /api/v1/ideas/{id}/match`` — runs the four-layer
     :class:`~src.agents.idea_matcher.IdeaMatcher` and persists the
     ranked calls into ``idea_call_matches``.
  3. ``GET /api/v1/ideas/{id}/matches`` — reads the cached match list.

All idea rows are tenant-scoped via RLS; the routes additionally
resolve the caller's tenant for the INSERT path and for the
IdeaMatcher's cost-logging context.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.agents.idea_matcher import IdeaMatcher
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.embedder_dep import get_embedder
from src.core.llm_dep import get_llm_router
from src.core.tenant import resolve_tenant_and_role
from src.llm.router import LLMRouter
from src.rag.base import Embedder
from src.rag.corpus_manager import _vector_literal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ideas", tags=["ideas"])

_IDEA_LIST_LIMIT_DEFAULT = 50
_IDEA_LIST_LIMIT_MAX = 200


# ── Models ─────────────────────────────────────────────────────────────


class IdeaCreate(BaseModel):
    """Body for ``POST /api/v1/ideas``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=300)
    abstract: str = Field(min_length=20, max_length=8000)
    technology_angle: str | None = Field(default=None, max_length=2000)
    target_market: str | None = Field(default=None, max_length=2000)
    trl_estimate: int | None = Field(default=None, ge=1, le=9)
    budget_estimate_eur_min: float | None = Field(default=None, ge=0)
    budget_estimate_eur_max: float | None = Field(default=None, ge=0)
    team_size_estimate: int | None = Field(default=None, ge=1)
    sectors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source: str = Field(default="user_input")
    seed_call_id: UUID | None = None


class IdeaSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    abstract: str
    technology_angle: str | None
    target_market: str | None
    trl_estimate: int | None
    budget_estimate_eur_min: float | None
    budget_estimate_eur_max: float | None
    team_size_estimate: int | None
    sectors: list[str]
    keywords: list[str]
    distinctiveness_score: float | None
    status: str
    source: str
    seed_call_id: UUID | None
    created_at: datetime


class IdeaListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ideas: list[IdeaSummary]
    total: int


class CallMatchOut(BaseModel):
    """One ranked call in a match response."""

    model_config = ConfigDict(frozen=True)

    call_id: UUID
    total_score: float
    semantic_score: float
    keyword_overlap_score: float
    sector_score: float
    trl_fit_score: float
    budget_fit_score: float
    rationale_tr: str
    rationale_en: str
    identified_gaps: list[str]
    # Joined call summary fields so the FE can render cards without a
    # second round-trip.
    call_title: str | None = None
    programme_id: str | None = None
    deadline: str | None = None


class IdeaMatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    idea_id: UUID
    matches: list[CallMatchOut]
    filter_stats: dict[str, int]
    computed_at: str
    model_version: str


# ── Routes ─────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=IdeaSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project idea (embeds synchronously)",
)
async def create_idea(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    body: IdeaCreate,
) -> IdeaSummary:
    """Insert one project idea + its embedding.

    The embedding covers ``title + abstract + technology_angle`` — the
    fields that carry semantic signal for call matching. Budget / TRL /
    sectors are matched structurally, not semantically, so they stay
    out of the vector.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    if (
        body.budget_estimate_eur_min is not None
        and body.budget_estimate_eur_max is not None
        and body.budget_estimate_eur_max < body.budget_estimate_eur_min
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="budget_estimate_eur_max must be >= budget_estimate_eur_min",
        )

    embed_text = _idea_embed_text(
        body.title, body.abstract, body.technology_angle
    )
    vector = await embedder.embed(embed_text)

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO project_ideas (
              tenant_id, created_by, source, seed_call_id, title, abstract,
              technology_angle, target_market, trl_estimate,
              budget_estimate_eur_min, budget_estimate_eur_max,
              team_size_estimate, sectors, keywords, embedding, status,
              metadata
            ) VALUES (
              $1, $2, $3, $4, $5, $6,
              $7, $8, $9,
              $10, $11, $12,
              $13::text[], $14::text[], $15::vector(3072), 'active',
              '{}'::jsonb
            )
            RETURNING id, title, abstract, technology_angle, target_market,
                      trl_estimate, budget_estimate_eur_min,
                      budget_estimate_eur_max, team_size_estimate,
                      sectors, keywords, distinctiveness_score, status,
                      source, seed_call_id, created_at
            """,
            tenant_id,
            user_id,
            body.source,
            body.seed_call_id,
            body.title,
            body.abstract,
            body.technology_angle,
            body.target_market,
            body.trl_estimate,
            body.budget_estimate_eur_min,
            body.budget_estimate_eur_max,
            body.team_size_estimate,
            list(body.sectors),
            list(body.keywords),
            _vector_literal(vector),
        )
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="seed_call_id does not reference a known call",
        ) from exc

    assert row is not None
    logger.info(
        "idea_created",
        extra={
            "idea_id": str(row["id"]),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "source": body.source,
        },
    )
    return _row_to_idea_summary(row)


@router.get(
    "",
    response_model=IdeaListResponse,
    summary="List the tenant's project ideas",
)
async def list_ideas(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    status_filter: str | None = None,
    limit: int = _IDEA_LIST_LIMIT_DEFAULT,
    offset: int = 0,
) -> IdeaListResponse:
    """RLS scopes rows to the caller's tenant — no explicit filter needed."""

    capped_limit = max(1, min(limit, _IDEA_LIST_LIMIT_MAX))
    capped_offset = max(0, offset)

    if status_filter:
        rows = await conn.fetch(
            """
            SELECT id, title, abstract, technology_angle, target_market,
                   trl_estimate, budget_estimate_eur_min,
                   budget_estimate_eur_max, team_size_estimate, sectors,
                   keywords, distinctiveness_score, status, source,
                   seed_call_id, created_at, COUNT(*) OVER() AS _total
              FROM project_ideas
             WHERE status = $1
             ORDER BY created_at DESC
             LIMIT $2 OFFSET $3
            """,
            status_filter,
            capped_limit,
            capped_offset,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, title, abstract, technology_angle, target_market,
                   trl_estimate, budget_estimate_eur_min,
                   budget_estimate_eur_max, team_size_estimate, sectors,
                   keywords, distinctiveness_score, status, source,
                   seed_call_id, created_at, COUNT(*) OVER() AS _total
              FROM project_ideas
             ORDER BY created_at DESC
             LIMIT $1 OFFSET $2
            """,
            capped_limit,
            capped_offset,
        )

    total = int(rows[0]["_total"]) if rows else 0
    logger.info(
        "ideas_listed",
        extra={"user_id": str(user_id), "row_count": len(rows), "total": total},
    )
    return IdeaListResponse(
        ideas=[_row_to_idea_summary(r) for r in rows],
        total=total,
    )


@router.get(
    "/{idea_id}",
    response_model=IdeaSummary,
    summary="Fetch one project idea",
)
async def get_idea(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    idea_id: UUID,
) -> IdeaSummary:
    row = await conn.fetchrow(
        """
        SELECT id, title, abstract, technology_angle, target_market,
               trl_estimate, budget_estimate_eur_min,
               budget_estimate_eur_max, team_size_estimate, sectors,
               keywords, distinctiveness_score, status, source,
               seed_call_id, created_at
          FROM project_ideas
         WHERE id = $1
        """,
        idea_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"idea {idea_id} not found",
        )
    logger.info("idea_fetched", extra={"idea_id": str(idea_id), "user_id": str(user_id)})
    return _row_to_idea_summary(row)


@router.post(
    "/{idea_id}/match",
    response_model=IdeaMatchResponse,
    summary="Run the matcher: rank open calls for this idea",
)
async def match_idea(
    request: Request,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm_router: Annotated[LLMRouter, Depends(get_llm_router)],
    idea_id: UUID,
    top_k: int = 5,
) -> IdeaMatchResponse:
    """Run :class:`IdeaMatcher` and persist results into idea_call_matches.

    The matcher acquires its own connections across the four pipeline
    layers, so it needs the app-wide pool (``request.app.state.db_pool``)
    rather than the single request-scoped connection.
    """

    # Confirm the idea exists + belongs to the caller's tenant (RLS does
    # the actual scoping; this gives a clean 404 instead of a matcher
    # ValueError leaking out).
    idea_row = await conn.fetchrow(
        "SELECT id FROM project_ideas WHERE id = $1", idea_id
    )
    if idea_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"idea {idea_id} not found",
        )

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    matcher = IdeaMatcher(
        pool=pool,
        embedder=embedder,
        router=llm_router,
        tenant_id=tenant_id,
    )
    capped_top_k = max(1, min(top_k, 20))
    result = await matcher.match(idea_id, top_k=capped_top_k, persist=True)

    # Join call summary fields so the FE can render cards in one shot.
    call_ids = [m.call_id for m in result.matches]
    call_meta: dict[UUID, dict[str, Any]] = {}
    if call_ids:
        meta_rows = await conn.fetch(
            "SELECT id, title, programme_id, deadline FROM calls WHERE id = ANY($1::uuid[])",
            call_ids,
        )
        call_meta = {
            UUID(str(r["id"])): {
                "title": r["title"],
                "programme_id": r["programme_id"],
                "deadline": r["deadline"].isoformat() if r["deadline"] else None,
            }
            for r in meta_rows
        }

    matches_out = [
        CallMatchOut(
            call_id=m.call_id,
            total_score=m.total_score,
            semantic_score=m.semantic_score,
            keyword_overlap_score=m.keyword_overlap_score,
            sector_score=m.sector_score,
            trl_fit_score=m.trl_fit_score,
            budget_fit_score=m.budget_fit_score,
            rationale_tr=m.rationale_tr,
            rationale_en=m.rationale_en,
            identified_gaps=m.identified_gaps,
            call_title=call_meta.get(m.call_id, {}).get("title"),
            programme_id=call_meta.get(m.call_id, {}).get("programme_id"),
            deadline=call_meta.get(m.call_id, {}).get("deadline"),
        )
        for m in result.matches
    ]

    logger.info(
        "idea_matched",
        extra={
            "idea_id": str(idea_id),
            "tenant_id": str(tenant_id),
            "match_count": len(matches_out),
            "hard_pool": result.filter_stats.hard_filter_pool,
        },
    )
    return IdeaMatchResponse(
        idea_id=result.idea_id,
        matches=matches_out,
        filter_stats={
            "hard_filter_pool": result.filter_stats.hard_filter_pool,
            "semantic_pool": result.filter_stats.semantic_pool,
            "reranked": result.filter_stats.reranked,
        },
        computed_at=result.computed_at,
        model_version=result.model_version,
    )


@router.get(
    "/{idea_id}/matches",
    response_model=IdeaMatchResponse,
    summary="Read the cached match list for an idea",
)
async def get_idea_matches(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    idea_id: UUID,
) -> IdeaMatchResponse:
    """Returns the persisted idea_call_matches rows joined with call
    summary fields. Empty list if the matcher hasn't run yet."""

    idea_row = await conn.fetchrow(
        "SELECT id FROM project_ideas WHERE id = $1", idea_id
    )
    if idea_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"idea {idea_id} not found",
        )

    rows = await conn.fetch(
        """
        SELECT m.call_id, m.total_score, m.semantic_score,
               m.keyword_overlap_score, m.sector_score, m.trl_fit_score,
               m.budget_fit_score, m.rationale_tr, m.rationale_en,
               m.identified_gaps, m.computed_at, m.model_version,
               c.title AS call_title, c.programme_id, c.deadline
          FROM idea_call_matches m
          JOIN calls c ON c.id = m.call_id
         WHERE m.idea_id = $1
         ORDER BY m.total_score DESC NULLS LAST
        """,
        idea_id,
    )

    matches_out = [
        CallMatchOut(
            call_id=UUID(str(r["call_id"])),
            total_score=_f(r["total_score"]),
            semantic_score=_f(r["semantic_score"]),
            keyword_overlap_score=_f(r["keyword_overlap_score"]),
            sector_score=_f(r["sector_score"]),
            trl_fit_score=_f(r["trl_fit_score"]),
            budget_fit_score=_f(r["budget_fit_score"]),
            rationale_tr=r["rationale_tr"] or "",
            rationale_en=r["rationale_en"] or "",
            identified_gaps=list(r["identified_gaps"] or []),
            call_title=r["call_title"],
            programme_id=r["programme_id"],
            deadline=r["deadline"].isoformat() if r["deadline"] else None,
        )
        for r in rows
    ]
    computed_at = (
        rows[0]["computed_at"].isoformat() if rows else ""
    )
    model_version = rows[0]["model_version"] if rows else ""

    logger.info(
        "idea_matches_read",
        extra={"idea_id": str(idea_id), "user_id": str(user_id), "count": len(rows)},
    )
    return IdeaMatchResponse(
        idea_id=idea_id,
        matches=matches_out,
        filter_stats={},
        computed_at=computed_at,
        model_version=model_version or "",
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _idea_embed_text(
    title: str, abstract: str, technology_angle: str | None
) -> str:
    parts = [title, abstract]
    if technology_angle:
        parts.append(technology_angle)
    return "\n\n".join(parts)


def _row_to_idea_summary(row: asyncpg.Record) -> IdeaSummary:
    return IdeaSummary(
        id=UUID(str(row["id"])),
        title=str(row["title"]),
        abstract=str(row["abstract"]),
        technology_angle=row["technology_angle"],
        target_market=row["target_market"],
        trl_estimate=row["trl_estimate"],
        budget_estimate_eur_min=_f_opt(row["budget_estimate_eur_min"]),
        budget_estimate_eur_max=_f_opt(row["budget_estimate_eur_max"]),
        team_size_estimate=row["team_size_estimate"],
        sectors=list(row["sectors"] or []),
        keywords=list(row["keywords"] or []),
        distinctiveness_score=_f_opt(row["distinctiveness_score"]),
        status=str(row["status"]),
        source=str(row["source"]),
        seed_call_id=(
            UUID(str(row["seed_call_id"])) if row["seed_call_id"] else None
        ),
        created_at=row["created_at"],
    )


def _f(value: Any) -> float:
    """Numeric → float, defaulting to 0.0 for NULL (score columns)."""

    return float(value) if value is not None else 0.0


def _f_opt(value: Any) -> float | None:
    return float(value) if value is not None else None


__all__ = [
    "CallMatchOut",
    "IdeaCreate",
    "IdeaListResponse",
    "IdeaMatchResponse",
    "IdeaSummary",
    "router",
]
