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
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from src.agents.base import AgentInput
from src.agents.compliance_reviewer import ComplianceReport, ComplianceReviewer
from src.agents.hallucination_hunter import HallucinationHunter, HuntReport
from src.api.routes.citations import build_citation_cache
from src.billing.quota import consume_quota
from src.citations import CitationVerifier
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)
from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.config import SettingsDep
from src.core.db import get_db
from src.core.llm_dep import get_llm_router
from src.core.rate_limit import LLM_CALL, RateLimitDecision, rate_limit
from src.core.tenant import resolve_tenant_and_role
from src.llm.router import LLMRouter
from src.orchestrator.sse_publisher import channel_for
from src.programs import get_module
from src.programs._tubitak_base import ProdisField, TUBITAKBaseModule
from src.programs.base import ValidationIssue
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


# ── CRUD response/request models (Sprint 4 MVP flow) ───────────────────


ProposalStatus = Literal[
    "draft",
    "brief_complete",
    "generating",
    "draft_complete",
    "in_review",
    "validated",
    "exported",
    "submitted",
    "funded",
    "rejected",
    "archived",
]


class ProposalCreate(BaseModel):
    """Body for ``POST /api/v1/proposals``.

    Minimum viable: caller picks a programme + language; everything else
    (call link, brief content, draft body) can be filled in later via
    ``PATCH``. Sprint 4 pilot flow uses this to seed an empty proposal
    that the user then runs through the brief form."""

    model_config = ConfigDict(extra="forbid")

    programme_id: str
    language: Literal["tr", "en"]
    title: str | None = None
    acronym: str | None = None
    call_id: UUID | None = None
    brief: dict[str, Any] | None = None


class ProposalUpdate(BaseModel):
    """Body for ``PATCH /api/v1/proposals/{id}``.

    Every field optional — the route applies only the supplied keys so
    a partial save (e.g. only ``brief``) doesn't clobber an in-flight
    draft. Status transitions are intentionally constrained: callers
    can move forward (draft → brief_complete) or to ``archived`` but
    saga-managed statuses (``generating`` / ``draft_complete``) must
    not be set by the HTTP caller."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    acronym: str | None = None
    call_id: UUID | None = None
    brief: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    status: Literal["draft", "brief_complete", "in_review", "archived"] | None = None


class ProposalSummary(BaseModel):
    """Row in ``GET /api/v1/proposals`` list response.

    Heavy fields (``brief``, ``draft``, ``compliance_report``) are
    omitted so the list can render dozens of rows without bloating the
    payload. The FE calls ``GET /proposals/{id}`` for the detail view."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    programme_id: str
    language: str
    title: str | None
    acronym: str | None
    status: ProposalStatus
    call_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime | None
    word_count: int
    llm_cost_usd: float


class ProposalListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposals: list[ProposalSummary]


class ProposalDetail(BaseModel):
    """Full row of ``GET /api/v1/proposals/{id}``.

    Carries every column the editor + validation surface needs. Heavy
    jsonb fields (``brief``, ``draft``, ``compliance_report``) are
    typed loose because their shape is programme-specific; the FE knows
    the schema via :class:`src.programs.base.BaseProgramModule`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    programme_id: str
    language: str
    title: str | None
    acronym: str | None
    status: ProposalStatus
    call_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime | None
    brief: dict[str, Any]
    draft: dict[str, Any]
    compliance_report: dict[str, Any]
    distinctiveness_score: float | None
    ai_disclosure_text: str | None
    word_count: int
    page_count: int
    llm_cost_usd: float


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


class ValidationReport(BaseModel):
    """Combined output of ``POST /proposals/{id}/validate``.

    Wraps the existing :class:`ComplianceReport` (formal rules + LLM
    depth check) with the :class:`HuntReport` (citation + claim
    verification) so the FE can render both surfaces from a single
    request. S3.D13.T1 completion — Sprint 3 shipped the agent itself,
    this PR wires it into the re-validate path that the editor uses
    after manual edits to the draft.

    Export gate:
    - ``compliance.passed=False`` (existing semantics) — formal rule
      blocker present.
    - ``hallucination_hunter.recommendation == "block_export"`` —
      fabricated citation OR sample claim pass rate below threshold.

    Either signal disables the export button on the FE.
    """

    model_config = ConfigDict(frozen=True)

    compliance: ComplianceReport
    hallucination_hunter: HuntReport | None = None
    """``None`` when the hunt couldn't run (e.g. zero citations). The
    FE treats ``None`` the same as ``recommendation="ok"``."""


@router.post(
    "/{proposal_id}/validate",
    response_model=ValidationReport,
    summary="Run compliance + hallucination hunter against the persisted draft",
)
async def validate_proposal(
    proposal_id: UUID,
    user_id: CurrentUserId,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    router_inst: Annotated[LLMRouter, Depends(get_llm_router)],
    _rate_check: Annotated[
        RateLimitDecision, Depends(rate_limit(LLM_CALL))
    ],
) -> ValidationReport:
    """Re-run the export gate against the proposal's persisted draft.

    Used after the user edits the draft manually — the saga only runs
    compliance + hunt once during generation; this endpoint surfaces the
    "Re-validate" button. Runs both checks back-to-back so the FE gets
    a fresh export decision in a single round trip.

    Persists the merged compliance_report (with the hunt embedded under
    ``compliance_report.hallucination_hunter``, mirroring the saga's
    persistence shape in ``orchestrator/draft_generator.py``).

    - 200: returns the fresh :class:`ValidationReport`.
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

    # 1. Compliance Reviewer (formal rules + HE depth check).
    compliance_output = await ComplianceReviewer(router=router_inst, conn=conn).run(
        agent_input
    )
    compliance = ComplianceReport.model_validate(compliance_output.output)

    # 2. Hallucination Hunter — share a single httpx client across the
    # citation + (optional) abstract fetches so the verifier doesn't
    # spin up a connection pool per call.
    cache = await build_citation_cache(settings)
    hunt: HuntReport | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        verifier = CitationVerifier(client=client, cache=cache)
        hunter_output = await HallucinationHunter(
            verifier=verifier, router=router_inst
        ).run(agent_input)
    if hunter_output.status == "completed" and hunter_output.output:
        hunt = HuntReport.model_validate(hunter_output.output)

    # 3. Merge hunt block into compliance — if fabricated citations
    # exist we must surface them as a blocker so the FE export button
    # disables. We add ONE issue summarising the hunt verdict instead
    # of replicating every flagged citation; the full list lives in the
    # hunt report itself.
    if hunt is not None and hunt.recommendation == "block_export":
        compliance = _attach_hunt_blocker(compliance, hunt)

    # 4. Persist — keep the saga's shape (compliance fields + nested
    # hallucination_hunter) so the existing FE consumers don't break.
    persisted_report = {
        **compliance.model_dump(mode="json"),
        "hallucination_hunter": hunt.model_dump(mode="json") if hunt else None,
    }
    await conn.execute(
        """
        update proposals
        set compliance_report = $1::jsonb,
            ai_disclosure_text = $2,
            updated_at = now()
        where id = $3
        """,
        json.dumps(persisted_report),
        compliance.ai_disclosure_text,
        proposal_id,
    )

    logger.info(
        "validate_completed",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "passed": compliance.passed,
            "issue_count": len(compliance.issues),
            "hunt_recommendation": hunt.recommendation if hunt else None,
            "fabricated_citations": hunt.fabricated if hunt else 0,
            "claim_pass_rate": hunt.claim_check_pass_rate if hunt else None,
        },
    )
    return ValidationReport(compliance=compliance, hallucination_hunter=hunt)


def _attach_hunt_blocker(
    compliance: ComplianceReport, hunt: HuntReport
) -> ComplianceReport:
    """Return a copy of ``compliance`` with one extra blocker issue + ``passed=False``.

    Keeps ``ComplianceReport`` frozen-safe (Pydantic ``frozen=True``)
    while still surfacing the hunt verdict in the same ``issues`` list
    the FE already renders. The issue ``code="hunt_blocked"`` is the
    discriminant the FE keys off to render the red "fabricated
    citations" badge.
    """

    fabricated_or_unfound = hunt.fabricated + hunt.not_found
    pass_rate = hunt.claim_check_pass_rate
    pass_rate_str = f"{pass_rate:.0%}" if pass_rate is not None else "—"

    blocker = ValidationIssue(
        severity="blocker",
        section=None,
        code="hunt_blocked",
        message_tr=(
            f"Hallucination Hunter ihracatı engelledi: "
            f"{fabricated_or_unfound} doğrulanamayan atıf, "
            f"claim destekleme oranı {pass_rate_str}."
        ),
        message_en=(
            f"Hallucination Hunter blocked export: "
            f"{fabricated_or_unfound} unverified citation(s), "
            f"claim support rate {pass_rate_str}."
        ),
        suggestion=(
            "Open each flagged citation in the editor and either fix "
            "the source reference or remove the unsupported claim."
        ),
    )
    return compliance.model_copy(
        update={
            "passed": False,
            "issues": [*compliance.issues, blocker],
        }
    )


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


_PROPOSAL_LIST_LIMIT_DEFAULT = 50
_PROPOSAL_LIST_LIMIT_MAX = 200


# ── Proposals CRUD (Sprint 4 MVP flow) ─────────────────────────────────


@router.post(
    "",
    response_model=ProposalDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new proposal (Sprint 4 MVP)",
)
async def create_proposal(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: ProposalCreate,
) -> ProposalDetail:
    """Seed an empty proposal for the caller's tenant.

    The status starts at ``draft``; the FE then drives the brief form
    + generation saga. Programme validation: the ``programme_id`` MUST
    match a row in :class:`programmes` (DB FK enforces this — we
    surface a 422 instead of asyncpg's raw error).

    - 201: created; returns the full :class:`ProposalDetail` so the FE
      can route straight to the detail page without a follow-up GET.
    - 404: caller has no active tenant (soft-deleted users).
    - 422: programme_id not found or call_id mismatch.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into proposals (
                  tenant_id, created_by, call_id, programme_id,
                  title, acronym, language, brief, status
                ) values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'draft')
                returning *
                """,
                tenant_id,
                user_id,
                body.call_id,
                body.programme_id,
                body.title,
                body.acronym,
                body.language,
                json.dumps(body.brief or {}),
            )
            assert row is not None
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="proposal.created",
                resource_type="proposal",
                resource_id=UUID(str(row["id"])),
                diff={
                    "programme": body.programme_id,
                    "language": body.language,
                },
            )
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"programme_id={body.programme_id} or call_id={body.call_id} "
                "does not exist"
            ),
        ) from exc

    logger.info(
        "proposal_created",
        extra={
            "proposal_id": str(row["id"]),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "programme_id": body.programme_id,
        },
    )
    return _row_to_proposal_detail(row)


@router.get(
    "",
    response_model=ProposalListResponse,
    summary="List proposals in the caller's tenant",
)
async def list_proposals(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    limit: int = _PROPOSAL_LIST_LIMIT_DEFAULT,
    status_filter: ProposalStatus | None = None,
) -> ProposalListResponse:
    """Return the tenant's proposals, newest first.

    The summary view omits heavy jsonb columns (``brief``, ``draft``,
    ``compliance_report``) so a dashboard with 50+ rows stays cheap.
    The full detail comes from :func:`get_proposal` when the user
    clicks into a row.

    - 200: list (possibly empty).
    - 404: caller has no active tenant.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)
    capped_limit = max(1, min(limit, _PROPOSAL_LIST_LIMIT_MAX))

    if status_filter is not None:
        rows = await conn.fetch(
            """
            select id, programme_id, language, title, acronym, status,
                   call_id, created_by, created_at, updated_at,
                   word_count, llm_cost_usd
              from proposals
             where tenant_id = $1
               and status = $2
             order by created_at desc
             limit $3
            """,
            tenant_id,
            status_filter,
            capped_limit,
        )
    else:
        rows = await conn.fetch(
            """
            select id, programme_id, language, title, acronym, status,
                   call_id, created_by, created_at, updated_at,
                   word_count, llm_cost_usd
              from proposals
             where tenant_id = $1
             order by created_at desc
             limit $2
            """,
            tenant_id,
            capped_limit,
        )

    return ProposalListResponse(
        proposals=[_row_to_proposal_summary(r) for r in rows]
    )


@router.get(
    "/{proposal_id}",
    response_model=ProposalDetail,
    summary="Read a single proposal in full",
)
async def get_proposal(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> ProposalDetail:
    """Fetch the full row (brief + draft + compliance + AI disclosure).

    Cross-tenant guard: if the proposal exists in another tenant we
    return 404 (not 403) — same existence-obscuring policy used by
    versions / comments.

    - 200: detail.
    - 404: proposal not found OR belongs to a different tenant.
    """

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)
    row = await conn.fetchrow(
        "select * from proposals where id = $1 and tenant_id = $2",
        proposal_id,
        tenant_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )
    return _row_to_proposal_detail(row)


@router.patch(
    "/{proposal_id}",
    response_model=ProposalDetail,
    summary="Partial-update a proposal (title / brief / draft / status)",
)
async def update_proposal(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: ProposalUpdate,
) -> ProposalDetail:
    """Apply only the supplied fields to the proposal row.

    Status transitions are deliberately narrow: the HTTP caller can
    push ``draft → brief_complete``, mark a draft for review
    (``in_review``), or archive (``archived``). Saga-managed
    statuses (``generating`` / ``draft_complete`` / ``validated``)
    must NOT be set by the HTTP layer — those flip server-side as the
    pipeline progresses.

    - 200: updated; returns the fresh :class:`ProposalDetail`.
    - 400: no fields supplied (avoid no-op writes).
    - 404: proposal not found or wrong tenant.
    - 422: ``call_id`` references a non-existent row.
    """

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="patch body must contain at least one field",
        )

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    # Cross-tenant guard — 404 if it isn't ours.
    owner = await conn.fetchval(
        "select tenant_id from proposals where id = $1",
        proposal_id,
    )
    if owner is None or UUID(str(owner)) != tenant_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )

    # Build the SET clause dynamically — keep it explicit so a future
    # field add can't slip past validation. (asyncpg has no native
    # named-parameter expansion; this is the standard pattern.)
    set_clauses: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    idx = 1
    for column, value in (
        ("title", updates.get("title", _UNSET)),
        ("acronym", updates.get("acronym", _UNSET)),
        ("call_id", updates.get("call_id", _UNSET)),
        ("status", updates.get("status", _UNSET)),
    ):
        if value is _UNSET:
            continue
        set_clauses.append(f"{column} = ${idx}")
        args.append(value)
        idx += 1
    if "brief" in updates:
        set_clauses.append(f"brief = ${idx}::jsonb")
        args.append(json.dumps(updates["brief"]))
        idx += 1
    if "draft" in updates:
        set_clauses.append(f"draft = ${idx}::jsonb")
        args.append(json.dumps(updates["draft"]))
        idx += 1

    args.extend([proposal_id, tenant_id])
    sql = (
        f"update proposals set {', '.join(set_clauses)} "
        f"where id = ${idx} and tenant_id = ${idx + 1} returning *"
    )

    try:
        async with conn.transaction():
            row = await conn.fetchrow(sql, *args)
            if row is None:
                # Lost the race — proposal disappeared between guard
                # and update.
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail=f"proposal {proposal_id} not found",
                )
            audit_diff: dict[str, Any] = {
                k: "set" for k in updates
            }
            if "status" in updates:
                audit_diff["new_status"] = updates["status"]
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="proposal.updated",
                resource_type="proposal",
                resource_id=proposal_id,
                diff=audit_diff,
            )
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"call_id={updates.get('call_id')} does not exist",
        ) from exc

    logger.info(
        "proposal_updated",
        extra={
            "proposal_id": str(proposal_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "fields": sorted(updates.keys()),
        },
    )
    return _row_to_proposal_detail(row)


@router.delete(
    "/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a proposal (admin or author only)",
)
async def delete_proposal(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> Response:
    """Hard-delete the proposal row + cascade children.

    Two callers may delete: the proposal's ``created_by`` author OR an
    owner/admin on the tenant. Cascade in the schema removes
    citations + provenance + versions + comments + audit rows that
    reference the proposal.

    - 204: deleted (idempotent — also returns 204 if it was already gone).
    - 403: caller is neither author nor admin.
    - 404: proposal not found in caller's tenant.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    row = await conn.fetchrow(
        "select created_by from proposals where id = $1 and tenant_id = $2",
        proposal_id,
        tenant_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"proposal {proposal_id} not found"
        )

    is_author = row["created_by"] is not None and UUID(str(row["created_by"])) == user_id
    is_admin = role in {"owner", "admin"}
    if not (is_author or is_admin):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="only the proposal author or a tenant owner/admin may delete this",
        )

    async with conn.transaction():
        await conn.execute(
            "delete from proposals where id = $1 and tenant_id = $2",
            proposal_id,
            tenant_id,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.deleted",
            resource_type="proposal",
            resource_id=proposal_id,
            diff={"by_role": role},
        )

    logger.info(
        "proposal_deleted",
        extra={
            "proposal_id": str(proposal_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "by_role": role,
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Sentinel for ``ProposalUpdate.model_dump(exclude_unset=True)`` — we
# also need to distinguish "field omitted" from "field set to None"
# when the caller wants to CLEAR ``title`` / ``acronym``. None as a
# Pydantic default means the field is updatable; a separate sentinel
# only matters inside the dynamic SQL builder above.
_UNSET = object()


def _row_to_proposal_summary(row: asyncpg.Record) -> ProposalSummary:
    return ProposalSummary(
        id=UUID(str(row["id"])),
        programme_id=str(row["programme_id"]),
        language=str(row["language"]),
        title=row["title"],
        acronym=row["acronym"],
        status=str(row["status"]),  # type: ignore[arg-type]
        call_id=UUID(str(row["call_id"])) if row["call_id"] else None,
        created_by=UUID(str(row["created_by"])) if row["created_by"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        word_count=int(row["word_count"] or 0),
        llm_cost_usd=float(row["llm_cost_usd"] or 0),
    )


def _row_to_proposal_detail(row: asyncpg.Record) -> ProposalDetail:
    return ProposalDetail(
        id=UUID(str(row["id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        programme_id=str(row["programme_id"]),
        language=str(row["language"]),
        title=row["title"],
        acronym=row["acronym"],
        status=str(row["status"]),  # type: ignore[arg-type]
        call_id=UUID(str(row["call_id"])) if row["call_id"] else None,
        created_by=UUID(str(row["created_by"])) if row["created_by"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        brief=_loads(row["brief"]),
        draft=_loads(row["draft"]),
        compliance_report=_loads(row["compliance_report"]),
        distinctiveness_score=float(row["distinctiveness_score"])
        if row["distinctiveness_score"] is not None
        else None,
        ai_disclosure_text=row["ai_disclosure_text"],
        word_count=int(row["word_count"] or 0),
        page_count=int(row["page_count"] or 0),
        llm_cost_usd=float(row["llm_cost_usd"] or 0),
    )


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
    "ProposalCreate",
    "ProposalDetail",
    "ProposalListResponse",
    "ProposalStatus",
    "ProposalSummary",
    "ProposalUpdate",
    "ValidationReport",
    "router",
]
