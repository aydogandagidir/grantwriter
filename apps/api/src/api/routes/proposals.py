"""Proposal-scoped HTTP endpoints.

S1.D5.T1 only ships the export trigger; full proposal CRUD lands in
S1.D4 / S2.

POST /api/v1/proposals/{proposal_id}/export
    Enqueues the DOCX export Celery task and returns the task id.
    Auth: bearer JWT (Supabase). Authorization is enforced at the
    DB layer via RLS once the proposal-load logic lands; for now
    the endpoint accepts any authenticated user.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

from src.core.auth import CurrentUserId
from src.tasks.exports import generate_proposal_docx_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])


class ExportEnqueued(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str = "queued"
    proposal_id: UUID


@router.post(
    "/{proposal_id}/export",
    response_model=ExportEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue DOCX export for a proposal",
)
async def enqueue_export(
    proposal_id: UUID,
    user_id: CurrentUserId,
    proposal: dict[str, Any] | None = None,
) -> ExportEnqueued:
    """Enqueue the export task and return the Celery task id.

    The proposal payload is currently passed in the body for v1
    (the orchestrator already has the dict in memory). When the
    proposals service lands (S2) the handler will load it from DB
    by id; the URL contract stays the same.
    """

    payload = dict(proposal or {})
    payload.setdefault("id", str(proposal_id))

    async_result = generate_proposal_docx_task.delay(payload)
    logger.info(
        "export_enqueued",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "task_id": async_result.id,
        },
    )
    return ExportEnqueued(job_id=str(async_result.id), proposal_id=proposal_id)


__all__ = ["router"]
