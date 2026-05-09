"""Proposals routes (subset).

Currently only the distinctiveness endpoint. CRUD lands in a later sprint task.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from src.compliance.distinctiveness import (
    DistinctivenessScore,
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)
from src.core.auth import User, get_current_user
from src.core.db import get_db

router = APIRouter(prefix="/proposals", tags=["proposals"])


@router.get(
    "/{proposal_id}/distinctiveness",
    response_model=DistinctivenessScore,
    summary="Score a proposal's distinctiveness against CORDIS funded projects",
)
async def get_distinctiveness(
    proposal_id: UUID,
    _user: Annotated[User, Depends(get_current_user)],
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> DistinctivenessScore:
    scorer = DistinctivenessScorer()
    try:
        return await scorer.score(proposal_id, conn)
    except ProposalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProposalNotReadyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
