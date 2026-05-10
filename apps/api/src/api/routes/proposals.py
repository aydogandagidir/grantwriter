"""Proposal-scoped HTTP endpoints.

Routes (per docs/05 §3.4–3.6):

- ``POST /proposals/{id}/export``         — DOCX export Celery task (S1.D5).
- ``GET  /proposals/{id}/distinctiveness`` — DistinctivenessScorer (S2.D8).
- ``POST /proposals/{id}/generate``       — kicks off the saga (S2.D7).
- ``POST /proposals/{id}/validate``       — runs ComplianceReviewer sync (S2.D9).
- ``GET  /proposals/{id}/ai-disclosure``  — reads the persisted disclosure (S2.D9).
- ``GET  /proposals/{id}/stream``         — SSE stream of saga events (S2.D7).

Auth: bearer JWT (Supabase) on every route. RLS enforces tenant scoping
at the DB layer once :class:`get_db` returns a connection bound to the
caller's JWT — for v1 the proposal-load logic accepts any authenticated
user.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from src.agents.base import AgentInput
from src.agents.compliance_reviewer import ComplianceReport, ComplianceReviewer
from src.billing.quota import consume_quota
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.llm_dep import get_llm_router
from src.core.rate_limit import LLM_CALL, RateLimitDecision, rate_limit
from src.llm.router import LLMRouter
from src.orchestrator.sse_publisher import channel_for
from src.programs import get_module
from src.programs._tubitak_base import ProdisField, TUBITAKBaseModule
from src.tasks.exports import (
    generate_proposal_docx_task,
    generate_proposal_xlsx_task,
)
from src.tasks.orchestrator import generate_draft_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])


# ── Response models ────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    """Body for ``POST /proposals/{id}/export``.

    Per docs/05 §3.7. ``format`` selects between DOCX (programme-specific
    full proposal) and XLSX (HE Lump Sum budget). ``proposal`` carries
    the in-memory payload until the proposals service lands (S2+).
    """

    model_config = ConfigDict(extra="ignore")

    format: Literal["docx", "xlsx"] = "docx"
    proposal: dict[str, Any] | None = None


class ExportEnqueued(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str = "queued"
    proposal_id: UUID
    format: Literal["docx", "xlsx"] = "docx"


class GenerateEnqueued(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    proposal_id: UUID
    estimated_duration_seconds: int = 600
    status_url: str
    stream_url: str


class AIDisclosureResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str


class ProdisFieldsResponse(BaseModel):
    """Response shape for ``GET /proposals/{id}/prodis-fields``."""

    model_config = ConfigDict(frozen=True)

    programme_id: str
    fields: list[ProdisField]


# ── Existing routes (preserved) ────────────────────────────────────────


@router.post(
    "/{proposal_id}/export",
    response_model=ExportEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue DOCX or XLSX export for a proposal",
)
async def enqueue_export(
    proposal_id: UUID,
    user_id: CurrentUserId,
    request: ExportRequest | None = None,
) -> ExportEnqueued:
    """Enqueue an export task and return the Celery task id.

    ``format=docx`` (default) renders the full proposal through the
    programme module; ``format=xlsx`` renders the Lump Sum budget
    workbook (HE only — TÜBİTAK / KOSGEB return None and the task
    skips the upload).

    The proposal payload is currently passed in the body for v1
    (the orchestrator already has the dict in memory). When the
    proposals service lands (S2+) the handler will load it from DB
    by id; the URL contract stays the same.
    """

    body = request or ExportRequest()
    payload = dict(body.proposal or {})
    payload.setdefault("id", str(proposal_id))

    if body.format == "xlsx":
        async_result = generate_proposal_xlsx_task.delay(payload)
    else:
        async_result = generate_proposal_docx_task.delay(payload)

    logger.info(
        "export_enqueued",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "task_id": async_result.id,
            "format": body.format,
        },
    )
    return ExportEnqueued(
        job_id=str(async_result.id), proposal_id=proposal_id, format=body.format
    )


@router.get(
    "/{proposal_id}/distinctiveness",
    response_model=DistinctivenessScore,
    summary="Score a proposal's distinctiveness against CORDIS funded projects",
)
async def get_distinctiveness(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> DistinctivenessScore:
    """Run the embedding-based distinctiveness scorer.

    See ``src/compliance/distinctiveness.py`` for the algorithm.

    - 200: returns score (level=distinctive|warning|critical), or level=unknown
      when no comparable CORDIS projects exist for the call's topic.
    - 404: proposal does not exist.
    - 422: proposal exists but lacks the inputs (no excellence_md, no linked
      call/topic).
    - 503: DATABASE_URL not configured (handled by ``get_db``).
    """

    scorer = DistinctivenessScorer()
    try:
        score = await scorer.score(proposal_id, conn)
    except ProposalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProposalNotReadyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    logger.info(
        "distinctiveness_scored",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "level": score.level,
            "score": score.score,
        },
    )
    return score


# ── New routes (S2.D7 / S2.D9) ─────────────────────────────────────────


@router.post(
    "/{proposal_id}/generate",
    response_model=GenerateEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue the 7-agent draft generation saga",
)
async def enqueue_generation(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    _rate_check: Annotated[
        RateLimitDecision, Depends(rate_limit(LLM_CALL))
    ],
) -> GenerateEnqueued:
    """Kick off :func:`generate_draft_task` and return the job + stream URLs.

    The saga itself runs in the Celery worker. The HTTP layer returns
    immediately so the frontend can subscribe to the SSE stream.

    Two gates run before the enqueue:
    1. **Rate limit** — 10 / 60s per user (docs/09 §8). Throttles a
       generate-loop attack inside a single minute.
    2. **Plan quota** — atomic per-month counter on
       ``tenants.monthly_proposals_used`` against
       ``monthly_proposal_limit`` (Starter 3, Pro 15, Agency unlimited).
       402 + ``X-Plan-*`` headers when exhausted; FE upsells.

    A failed saga does NOT refund the quota — the user pressed Generate
    and the LLM provider already charged for the burst.
    """

    tenant_id = await conn.fetchval(
        "select tenant_id from proposals where id = $1", proposal_id
    )
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"proposal {proposal_id} not found",
        )

    quota = await consume_quota(conn, tenant_id=UUID(str(tenant_id)))
    if not quota.allowed:
        snap = quota.snapshot
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "monthly_quota_exceeded",
                "plan": snap.plan,
                "limit": snap.monthly_limit,
                "used": snap.used_this_month,
                "period_start": snap.period_start.isoformat(),
            },
            headers={
                "X-Plan-Limit": str(snap.monthly_limit),
                "X-Plan-Used": str(snap.used_this_month),
                "X-Plan-Remaining": "0",
            },
        )

    async_result = generate_draft_task.delay(str(proposal_id))
    logger.info(
        "generate_enqueued",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "task_id": async_result.id,
            "plan": quota.snapshot.plan,
            "quota_used": quota.snapshot.used_this_month,
            "quota_limit": quota.snapshot.monthly_limit,
        },
    )
    return GenerateEnqueued(
        job_id=str(async_result.id),
        proposal_id=proposal_id,
        status_url=f"/api/v1/jobs/{async_result.id}",
        stream_url=f"/api/v1/proposals/{proposal_id}/stream",
    )


@router.post(
    "/{proposal_id}/validate",
    response_model=ComplianceReport,
    summary="Run compliance review against the persisted draft",
)
async def validate_proposal(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    router_inst: Annotated[LLMRouter, Depends(get_llm_router)],
    _rate_check: Annotated[
        RateLimitDecision, Depends(rate_limit(LLM_CALL))
    ],
) -> ComplianceReport:
    """Re-run :class:`ComplianceReviewer` against the proposal's persisted draft.

    Used after the user edits the draft manually — the saga only runs
    compliance once during generation; this endpoint surfaces the
    "Re-validate" button. Persists the new compliance_report and
    ai_disclosure_text back to the proposal row.

    - 200: returns the fresh ComplianceReport.
    - 404: proposal not found.
    - 429: rate limit exceeded (10 / 60s); ``Retry-After`` header present.
    """

    row = await conn.fetchrow(
        """
        select tenant_id, programme_id, language, brief, draft
        from proposals
        where id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )

    draft = _loads(row["draft"])
    brief = _loads(row["brief"])

    agent_input = AgentInput(
        proposal_id=proposal_id,
        tenant_id=row["tenant_id"],
        programme_id=str(row["programme_id"]),
        language=row["language"],
        brief=brief,
        call={},
        previous_outputs={
            "excellence_writer": {
                "agent_id": "excellence_writer",
                "status": "completed",
                "output": {"excellence_md": str(draft.get("excellence_md") or "")},
            },
            "impact_writer": {
                "agent_id": "impact_writer",
                "status": "completed",
                "output": {"impact_md": str(draft.get("impact_md") or "")},
            },
            "implementation_writer": {
                "agent_id": "implementation_writer",
                "status": "completed",
                "output": {
                    "implementation_md": str(draft.get("implementation_md") or "")
                },
            },
        },
    )

    agent = ComplianceReviewer(router=router_inst, conn=conn)
    output = await agent.run(agent_input)

    # Persist
    await conn.execute(
        """
        update proposals
        set compliance_report = $1::jsonb,
            ai_disclosure_text = $2,
            updated_at = now()
        where id = $3
        """,
        json.dumps(output.output),
        output.output.get("ai_disclosure_text"),
        proposal_id,
    )

    logger.info(
        "validate_completed",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "passed": output.output.get("passed"),
            "issue_count": len(output.output.get("issues") or []),
        },
    )
    return ComplianceReport.model_validate(output.output)


@router.get(
    "/{proposal_id}/ai-disclosure",
    response_model=AIDisclosureResponse,
    summary="Read the persisted Horizon Europe AI disclosure text",
)
async def get_ai_disclosure(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> AIDisclosureResponse:
    """Return the AI disclosure text from the proposal row.

    The disclosure is generated by the saga (or the validate endpoint).
    Returns 404 if the proposal doesn't exist OR if no disclosure has
    been generated yet (e.g., draft never went through compliance).
    """

    row = await conn.fetchrow(
        "select ai_disclosure_text from proposals where id = $1", proposal_id
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )
    text = row["ai_disclosure_text"]
    if not text:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="AI disclosure not yet generated — run /generate or /validate first",
        )
    logger.info(
        "ai_disclosure_returned",
        extra={"proposal_id": str(proposal_id), "user_id": str(user_id)},
    )
    return AIDisclosureResponse(text=text)


@router.get(
    "/{proposal_id}/prodis-fields",
    response_model=ProdisFieldsResponse,
    summary="Render proposal as PRODİS-ready field-by-field plain text (TÜBİTAK)",
)
async def get_prodis_fields(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> ProdisFieldsResponse:
    """Return one ``ProdisField`` per TÜBİTAK PRODİS form field.

    The TÜBİTAK PRODİS portal has no public API — applicants paste the
    proposal section by section. This endpoint renders the persisted
    draft as 11 plain-text fields (Markdown stripped) so the frontend
    can show one Copy button per field.

    - 200: returns the field list (per docs/07 §4.3).
    - 404: proposal not found.
    - 422: programme is not a TÜBİTAK programme — PRODİS is
      TÜBİTAK-specific, other funders use different portals.
    """

    row = await conn.fetchrow(
        """
        select programme_id, draft, brief
        from proposals
        where id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )

    programme_id = str(row["programme_id"])
    try:
        module = get_module(programme_id)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if not isinstance(module, TUBITAKBaseModule):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"PRODİS export is TÜBİTAK-specific; programme {programme_id!r} "
                "uses a different submission portal."
            ),
        )

    proposal = {
        "draft": _loads(row["draft"]),
        "brief": _loads(row["brief"]),
    }
    fields = module.get_prodis_fields(proposal)

    logger.info(
        "prodis_fields_returned",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "programme_id": programme_id,
            "field_count": len(fields),
        },
    )
    return ProdisFieldsResponse(programme_id=programme_id, fields=fields)


@router.get(
    "/{proposal_id}/stream",
    summary="Server-Sent Events stream of saga progress",
)
async def stream_proposal(
    request: Request,
    proposal_id: UUID,
    user_id: CurrentUserId,
) -> EventSourceResponse:
    """Subscribe to the proposal's Redis Pub/Sub channel and stream events.

    Channel: ``proposal:{uuid}``. The saga publishes ``saga_started``,
    ``agent_started``, ``agent_completed`` / ``agent_failed``,
    ``completed``, and ``error`` events. The stream closes when it sees
    ``completed`` or ``error``.

    503 if Redis is not configured (server has no broker).
    """

    redis_client = await _get_redis(request)
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis not configured — SSE stream unavailable",
        )

    channel = channel_for(proposal_id)
    logger.info(
        "stream_subscribed",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "channel": channel,
        },
    )

    async def event_generator() -> Any:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    envelope = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                yield {
                    "event": envelope.get("event") or "message",
                    "data": json.dumps(envelope.get("data") or {}),
                    "id": envelope.get("id") or "",
                }
                if envelope.get("event") in ("completed", "error"):
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return EventSourceResponse(event_generator())


# ── Helpers ────────────────────────────────────────────────────────────


async def _get_redis(request: Request) -> Any:
    """Return the app's Redis client, opening lazily on first use.

    Stored on ``app.state.redis_client`` so subsequent SSE connections
    reuse the same client. Returns ``None`` if ``REDIS_URL`` is not
    configured — caller maps that to 503.
    """

    cached = getattr(request.app.state, "redis_client", None)
    if cached is not None:
        return cached

    from src.core.config import get_settings

    settings = get_settings()
    if settings.redis_url is None:
        return None

    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url.get_secret_value()
    )
    request.app.state.redis_client = client
    return client


def _loads(value: Any) -> dict[str, Any]:
    """Normalise asyncpg jsonb returns (str | dict | None) into a dict."""

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = [
    "AIDisclosureResponse",
    "ExportEnqueued",
    "GenerateEnqueued",
    "router",
]
