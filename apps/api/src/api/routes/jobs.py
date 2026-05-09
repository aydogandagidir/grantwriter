"""Job-status endpoint backed by Celery's result backend.

A "job" here is any Celery AsyncResult that the API enqueued — DOCX
export, draft generation, future budget/PDF exports. The shape per
docs/05 §3.4:

::

    GET /api/v1/jobs/{job_id}
    → 200 {
      "id": "<task-id>",
      "status": "queued|running|completed|failed",
      "result": {...}  // populated when status == completed
      "error": null
    }

Celery's native states (``PENDING``, ``STARTED``, ``SUCCESS``,
``FAILURE``, ``RETRY``, ``REVOKED``) are mapped onto the four
public states the spec promises so the frontend can switch on them
without leaking Celery internals.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from src.core.auth import CurrentUserId
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


JobStatus = Literal["queued", "running", "completed", "failed"]


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None


def _map_celery_state(state: str) -> JobStatus:
    """Translate Celery's state vocabulary to our four public states.

    PENDING covers both "queued" and "unknown task id" — Celery's result
    backend doesn't distinguish them, so we surface PENDING as "queued"
    and accept that an unknown id will sit in "queued" forever (the
    frontend times out client-side after the saga's expected duration).
    """

    if state == "SUCCESS":
        return "completed"
    if state in ("FAILURE", "REVOKED"):
        return "failed"
    if state in ("STARTED", "RETRY"):
        return "running"
    return "queued"


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get the current status of a Celery task",
)
async def get_job(
    job_id: UUID,
    _user_id: CurrentUserId,
) -> JobStatusResponse:
    """Read the AsyncResult for ``job_id`` from the result backend.

    No DB access — the result backend (Redis or in-memory) holds the
    state. Bearer-token auth is required so untrusted callers can't
    poll arbitrary task ids; per-tenant authorization will land when
    we persist task ownership in Sprint 3.
    """

    job_id_str = str(job_id)
    async_result = celery_app.AsyncResult(job_id_str)

    state = str(async_result.state or "PENDING")
    public_status = _map_celery_state(state)
    result_payload: dict[str, Any] | None = None
    error: str | None = None

    if public_status == "completed":
        raw = async_result.result
        result_payload = raw if isinstance(raw, dict) else {"value": raw}
    elif public_status == "failed":
        # ``async_result.result`` is the exception in failure states.
        error = str(async_result.result or "task failed without a message")

    return JobStatusResponse(
        id=job_id_str, status=public_status, result=result_payload, error=error
    )


__all__ = ["JobStatusResponse", "router"]
