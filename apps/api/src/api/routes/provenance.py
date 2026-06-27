"""Sentence-level provenance write + stats endpoints.

The ``proposal_provenance`` table is the persistence layer for the
TipTap editor's per-sentence ``source`` metadata (human /
ai-generated / ai-edited / imported / rag-retrieved). Two routes here:

- ``POST /api/v1/proposals/{id}/provenance`` — batch upsert: the FE
  ships the dirty sentences as one payload on debounce. Idempotent
  on ``(proposal_id, sentence_id)`` so the same sentence flipping
  ``ai-generated → ai-edited`` after the user touches it is a single
  UPDATE, not a duplicate row.
- ``GET /api/v1/proposals/{id}/provenance/stats`` — aggregated counts
  the FE renders as the AI disclosure preview before the
  compliance reviewer's final report.

Auth: member+ on both — the editor is a per-tenant tool, every team
member writes to the same draft. Tenant scoping uses
``proposals.tenant_id`` JOIN; cross-tenant access returns 404.

Why batch instead of per-sentence: the editor commits in bursts (one
debounce window may flush 20 sentences). A single POST per burst gives
us one round-trip + one transaction; per-sentence routes would be N
HTTP requests + N rows of audit noise.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proposals", tags=["provenance"])


_ProvenanceSource = Literal[
    "human", "ai-generated", "ai-edited", "imported", "rag-retrieved"
]
_MAX_SENTENCES_PER_BATCH = 500


# ── Models ─────────────────────────────────────────────────────────────


class SentenceRecord(BaseModel):
    """One row the FE wants persisted.

    ``content`` is the raw sentence text — needed for AI-disclosure
    aggregation (paragraph-level counts use these). ``source_citations``
    is the list of citation raw-text refs the sentence depends on; used
    by Hallucination Hunter's claim-verification stage.
    """

    model_config = ConfigDict(extra="forbid")

    sentence_id: str = Field(min_length=1, max_length=128)
    section: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=8000)
    source: _ProvenanceSource
    agent_id: str | None = Field(default=None, max_length=64)
    llm_model: str | None = Field(default=None, max_length=64)
    llm_tokens: int | None = Field(default=None, ge=0)
    source_citations: list[str] | None = None


class ProvenanceBatchRequest(BaseModel):
    """POST body — at most :data:`_MAX_SENTENCES_PER_BATCH` rows per call."""

    model_config = ConfigDict(extra="forbid")

    sentences: list[SentenceRecord] = Field(min_length=1)


class ProvenanceBatchResponse(BaseModel):
    """Counts so the FE can confirm the burst landed completely."""

    model_config = ConfigDict(frozen=True)

    upserted: int


class SourceCount(BaseModel):
    """One row of the stats aggregation."""

    model_config = ConfigDict(frozen=True)

    source: str
    count: int


class ProvenanceItem(BaseModel):
    """One row of the items list — what the editor reads on load."""

    model_config = ConfigDict(frozen=True)

    sentence_id: str
    section: str
    content: str
    source: str
    agent_id: str | None
    llm_model: str | None
    llm_tokens: int | None
    created_at: str  # ISO-8601


class ProvenanceListResponse(BaseModel):
    """``GET /provenance`` body.

    ``next_offset`` is a server-driven hint — null when there are no
    more rows. The FE doesn't need to compute total / page numbers; it
    keeps calling with the previous ``next_offset`` until null.
    """

    model_config = ConfigDict(frozen=True)

    items: list[ProvenanceItem]
    next_offset: int | None


class ProvenanceStatsResponse(BaseModel):
    """``GET /provenance/stats`` body.

    ``total`` is the sum over ``per_source`` for FE percentage display.
    ``per_agent`` and ``per_model`` are non-NULL aggregates the
    AI-disclosure template needs to fill ``models_used`` + ``agents_used``.
    """

    model_config = ConfigDict(frozen=True)

    total: int
    per_source: list[SourceCount]
    per_agent: list[SourceCount]
    per_model: list[SourceCount]


# ── Helpers ────────────────────────────────────────────────────────────


async def _load_proposal_in_tenant(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    tenant_id: UUID,
) -> None:
    """Verify the proposal exists in the caller's tenant; 404 otherwise."""

    exists = await conn.fetchval(
        "select 1 from proposals where id = $1 and tenant_id = $2",
        proposal_id,
        tenant_id,
    )
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="proposal not found",
        )


# ── Routes ─────────────────────────────────────────────────────────────


@router.post(
    "/{proposal_id}/provenance",
    response_model=ProvenanceBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch upsert sentence-level provenance rows",
)
async def upsert_provenance(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: ProvenanceBatchRequest,
) -> ProvenanceBatchResponse:
    """Upsert each sentence keyed by ``(proposal_id, sentence_id)``.

    Status codes:
    - 200: returns the number of rows the batch touched.
    - 404: proposal not found in the caller's tenant.
    - 413: batch is larger than :data:`_MAX_SENTENCES_PER_BATCH`.
    """

    if len(body.sentences) > _MAX_SENTENCES_PER_BATCH:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"batch exceeds {_MAX_SENTENCES_PER_BATCH} sentences; "
                "split into smaller chunks"
            ),
        )

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _load_proposal_in_tenant(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    # asyncpg's ``executemany`` issues one prepared statement + N binds;
    # ON CONFLICT DO UPDATE keeps the (proposal_id, sentence_id) UNIQUE
    # constraint honest while letting source flips (ai-generated →
    # ai-edited) update the row in place. We don't return per-row state
    # because asyncpg's executemany discards RETURNING — the
    # ``upserted`` count is the input length (every row either inserted
    # or updated; partial failures abort the whole batch via the
    # transaction).
    async with conn.transaction():
        await conn.executemany(
            """
            insert into proposal_provenance (
              proposal_id, sentence_id, section, content, source,
              agent_id, llm_model, llm_tokens, source_citations
            ) values (
              $1, $2, $3, $4, $5, $6, $7, $8, $9
            )
            on conflict (proposal_id, sentence_id) do update set
              section = excluded.section,
              content = excluded.content,
              source = excluded.source,
              agent_id = excluded.agent_id,
              llm_model = excluded.llm_model,
              llm_tokens = excluded.llm_tokens,
              source_citations = excluded.source_citations
            """,
            [
                (
                    proposal_id,
                    sentence.sentence_id,
                    sentence.section,
                    sentence.content,
                    sentence.source,
                    sentence.agent_id,
                    sentence.llm_model,
                    sentence.llm_tokens,
                    sentence.source_citations,
                )
                for sentence in body.sentences
            ],
        )

    logger.info(
        "provenance_upserted",
        extra={
            "proposal_id": str(proposal_id),
            "user_id": str(user_id),
            "sentence_count": len(body.sentences),
        },
    )
    return ProvenanceBatchResponse(upserted=len(body.sentences))


@router.get(
    "/{proposal_id}/provenance/stats",
    response_model=ProvenanceStatsResponse,
    summary="Aggregated provenance counts for the AI-disclosure preview",
)
async def provenance_stats(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> ProvenanceStatsResponse:
    """Return source / agent / model counts.

    Status codes:
    - 200: aggregated counts.
    - 404: proposal not found in the caller's tenant.
    """

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _load_proposal_in_tenant(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    per_source = await conn.fetch(
        """
        select source as label, count(*) as count
          from proposal_provenance
         where proposal_id = $1
         group by source
         order by count desc
        """,
        proposal_id,
    )
    per_agent = await conn.fetch(
        """
        select agent_id as label, count(*) as count
          from proposal_provenance
         where proposal_id = $1 and agent_id is not null
         group by agent_id
         order by count desc
        """,
        proposal_id,
    )
    per_model = await conn.fetch(
        """
        select llm_model as label, count(*) as count
          from proposal_provenance
         where proposal_id = $1 and llm_model is not null
         group by llm_model
         order by count desc
        """,
        proposal_id,
    )

    total = sum(int(row["count"]) for row in per_source)
    return ProvenanceStatsResponse(
        total=total,
        per_source=[
            SourceCount(source=str(row["label"]), count=int(row["count"]))
            for row in per_source
        ],
        per_agent=[
            SourceCount(source=str(row["label"]), count=int(row["count"]))
            for row in per_agent
        ],
        per_model=[
            SourceCount(source=str(row["label"]), count=int(row["count"]))
            for row in per_model
        ],
    )


_DEFAULT_PAGE_LIMIT = 100
_MAX_PAGE_LIMIT = 500


@router.get(
    "/{proposal_id}/provenance",
    response_model=ProvenanceListResponse,
    summary="List sentence-level provenance rows for a proposal",
)
async def list_provenance(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    section: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Filter to a single section (excellence / impact / implementation).",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_LIMIT)] = _DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProvenanceListResponse:
    """Return one page of provenance rows for the editor pre-fill.

    The editor calls this on mount to reconstruct the saga-written
    marks atop the persisted draft markdown — without it the user
    sees the AI-generated text as plain prose with no source signal.

    Status codes:
    - 200: rows on this page (possibly empty if section has no entries).
    - 404: proposal not found in the caller's tenant.
    """

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _load_proposal_in_tenant(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    # Pull one extra row so we can tell whether the next page exists
    # without paying for a separate COUNT(*) — same trick the audit
    # log endpoint uses.
    rows = await conn.fetch(
        """
        select sentence_id, section, content, source,
               agent_id, llm_model, llm_tokens, created_at
          from proposal_provenance
         where proposal_id = $1
           and ($2::text is null or section = $2::text)
         order by section asc, created_at asc, sentence_id asc
         limit $3 offset $4
        """,
        proposal_id,
        section,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        ProvenanceItem(
            sentence_id=str(row["sentence_id"]),
            section=str(row["section"]),
            content=str(row["content"]),
            source=str(row["source"]),
            agent_id=(str(row["agent_id"]) if row["agent_id"] else None),
            llm_model=(str(row["llm_model"]) if row["llm_model"] else None),
            llm_tokens=(
                int(row["llm_tokens"]) if row["llm_tokens"] is not None else None
            ),
            created_at=row["created_at"].isoformat(),
        )
        for row in page
    ]
    next_offset = offset + limit if has_more else None
    return ProvenanceListResponse(items=items, next_offset=next_offset)


__all__ = [
    "ProvenanceBatchRequest",
    "ProvenanceBatchResponse",
    "ProvenanceItem",
    "ProvenanceListResponse",
    "ProvenanceStatsResponse",
    "SentenceRecord",
    "SourceCount",
    "router",
]
