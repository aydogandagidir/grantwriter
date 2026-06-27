"""Read-only catalog of grant programmes (Sprint 4 MVP backfill).

The schema in :mod:`infra/supabase/migrations/20260508120300_programmes.sql`
seeds the five Phase-1 programmes (TÜBİTAK 1501 + 1507, KOSGEB AR-GE,
Horizon Europe RIA, Cascade Funding / NLnet). This endpoint surfaces
that catalog to the FE so the "New proposal" flow can render a real
dropdown instead of hard-coding the list.

Auth: any authenticated user — programme metadata isn't tenant-scoped.
We still gate on the JWT so unauthenticated probes don't get a free
enumeration of programmes.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from src.core.auth import CurrentUserId
from src.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/programmes", tags=["programmes"])


# ── Models ─────────────────────────────────────────────────────────────


class ProgrammeSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name_tr: str
    name_en: str
    funder: str
    language: Literal["tr", "en", "both"]
    description_tr: str | None = None
    description_en: str | None = None
    active: bool = True


class ProgrammeListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    programmes: list[ProgrammeSummary]


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=ProgrammeListResponse,
    summary="List active grant programmes (read-only catalog)",
)
async def list_programmes(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> ProgrammeListResponse:
    """Return every ``active=true`` programme, ordered by funder + name.

    The 5-row Phase-1 seed comes from migration 003. Faz 2 will add
    Eurostars / MSCA / etc. — same shape, no FE change needed.
    """

    rows = await conn.fetch(
        """
        select id, name_tr, name_en, funder, language,
               description_tr, description_en, active
          from programmes
         where active = true
         order by funder asc, name_en asc
        """,
    )
    logger.info(
        "programmes_listed",
        extra={"user_id": str(user_id), "row_count": len(rows)},
    )
    return ProgrammeListResponse(
        programmes=[
            ProgrammeSummary(
                id=str(row["id"]),
                name_tr=str(row["name_tr"]),
                name_en=str(row["name_en"]),
                funder=str(row["funder"]),
                language=row["language"],
                description_tr=row["description_tr"],
                description_en=row["description_en"],
                active=bool(row["active"]),
            )
            for row in rows
        ]
    )


__all__ = ["ProgrammeListResponse", "ProgrammeSummary", "router"]
