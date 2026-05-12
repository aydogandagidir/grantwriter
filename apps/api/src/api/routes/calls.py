"""Open-call catalog endpoints (Sprint 4 MVP backfill).

The :mod:`scrapers` package will populate this table from EU F&T Portal,
NLnet, TÜBİTAK and KOSGEB feeds (S1.D3 / S2.D6 backlog). Until those
ship, the FE pilot flow lets the operator seed a call by hand via
``POST /api/v1/calls`` with ``source="manual"``.

Auth: any authenticated user — call metadata isn't tenant-scoped (the
funder publishes it). Manual creates write an audit row scoped to the
caller's tenant so we can attribute pilot-time seeded calls in retro.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])


_CALL_LIST_LIMIT_DEFAULT = 50
_CALL_LIST_LIMIT_MAX = 200


CallSource = Literal[
    "eu_ft_portal", "nlnet", "cascade", "tubitak", "kosgeb", "manual"
]
CallStatus = Literal["open", "closing_soon", "closed", "draft"]


# ── Models ─────────────────────────────────────────────────────────────


class CallCreate(BaseModel):
    """Body for ``POST /api/v1/calls``.

    Sprint 4 ships this manual path so the pilot demo can land before
    the EU F&T Portal scraper does. The ``source`` defaults to
    ``"manual"`` and the audit log records the caller; the scraper PR
    will set ``source`` to the funder-specific value and won't write
    an audit row.
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
    model_config = ConfigDict(frozen=True)

    id: UUID
    programme_id: str
    source: CallSource
    external_id: str
    title: str
    language: str
    status: CallStatus
    deadline: date | None
    call_url: str | None
    topic_keywords: list[str]
    scraped_at: datetime


class CallListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    calls: list[CallSummary]


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=CallListResponse,
    summary="List open calls, optionally filtered by programme",
)
async def list_calls(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    programme_id: str | None = None,
    status_filter: CallStatus | None = None,
    limit: int = _CALL_LIST_LIMIT_DEFAULT,
) -> CallListResponse:
    """Return calls ordered by deadline ascending (soonest first).

    ``programme_id`` filter narrows to one funder — the pilot UI fires
    this from the new-proposal modal once the programme is chosen.
    Default status filter is "open"; pass ``status_filter=closed`` to
    pull historical calls for retro / reference.
    """

    effective_status = status_filter or "open"
    capped_limit = max(1, min(limit, _CALL_LIST_LIMIT_MAX))

    if programme_id is not None:
        rows = await conn.fetch(
            """
            select id, programme_id, source, external_id, title, language,
                   status, deadline, call_url, topic_keywords, scraped_at
              from calls
             where status = $1 and programme_id = $2
             order by deadline asc nulls last, scraped_at desc
             limit $3
            """,
            effective_status,
            programme_id,
            capped_limit,
        )
    else:
        rows = await conn.fetch(
            """
            select id, programme_id, source, external_id, title, language,
                   status, deadline, call_url, topic_keywords, scraped_at
              from calls
             where status = $1
             order by deadline asc nulls last, scraped_at desc
             limit $2
            """,
            effective_status,
            capped_limit,
        )

    logger.info(
        "calls_listed",
        extra={
            "user_id": str(user_id),
            "programme_id": programme_id,
            "status": effective_status,
            "row_count": len(rows),
        },
    )
    return CallListResponse(
        calls=[_row_to_summary(r) for r in rows]
    )


@router.post(
    "",
    response_model=CallSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Seed a call manually (pilot bridge until scrapers ship)",
)
async def create_call(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: CallCreate,
) -> CallSummary:
    """Insert a call row + audit the action under the caller's tenant.

    Idempotent on ``(source, external_id)`` — re-running the same
    payload returns 409 so the pilot operator notices duplicates.

    - 201: created.
    - 409: another call with the same (source, external_id) exists.
    - 422: programme_id not in the catalog.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into calls (
                  programme_id, source, external_id, title, language,
                  call_text, call_url, call_pdf_url, deadline,
                  budget_total_eur, budget_per_project_min_eur,
                  budget_per_project_max_eur, trl_min, trl_max,
                  topic_keywords, raw_metadata, status
                ) values (
                  $1, $2, $3, $4, $5,
                  $6, $7, $8, $9,
                  $10, $11, $12, $13, $14,
                  $15::text[], $16::jsonb, 'open'
                )
                returning id, programme_id, source, external_id, title, language,
                          status, deadline, call_url, topic_keywords, scraped_at
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


# ── Helpers ────────────────────────────────────────────────────────────


def _row_to_summary(row: asyncpg.Record) -> CallSummary:
    return CallSummary(
        id=UUID(str(row["id"])),
        programme_id=str(row["programme_id"]),
        source=row["source"],
        external_id=str(row["external_id"]),
        title=str(row["title"]),
        language=str(row["language"]),
        status=row["status"],
        deadline=row["deadline"],
        call_url=row["call_url"],
        topic_keywords=list(row["topic_keywords"] or []),
        scraped_at=row["scraped_at"],
    )


__all__ = [
    "CallCreate",
    "CallListResponse",
    "CallSource",
    "CallStatus",
    "CallSummary",
    "router",
]
