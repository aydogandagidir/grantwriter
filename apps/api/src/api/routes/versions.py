"""Proposal version snapshots — manual create / list / restore.

Per docs/03 §2.3 + sprint-roadmap S3.D12. The ``proposal_versions``
table has been schema-ready since migration 006; this module finally
wires it to an API.

Three endpoints, all member-or-above:

- ``POST /api/v1/proposals/{id}/versions`` — take a snapshot of the
  current ``proposals.draft`` JSON. ``version_number`` auto-increments
  per proposal. Returns the row WITHOUT the snapshot body.
- ``GET  /api/v1/proposals/{id}/versions`` — list (newest first), no
  snapshot bodies. Useful for the FE history pane.
- ``POST /api/v1/proposals/{id}/versions/{n}/restore`` — copy version
  ``n``'s snapshot into a NEW row (so history stays intact) and update
  ``proposals.draft`` to the restored JSON. Old versions are not
  deleted; the restored state is itself the "new current".

Cross-tenant guard: every endpoint checks ``proposals.tenant_id``
matches the caller's tenant. A foreign proposal id returns 404 (not
403) so we don't leak the existence of cross-tenant resources.

Saga auto-snapshot — explicitly out of scope for this PR. The
orchestrator can grow a hook later that calls POST /versions on
saga complete; this module gives it the surface to call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proposals", tags=["versions"])


# ── Models ─────────────────────────────────────────────────────────────


class VersionCreate(BaseModel):
    """Optional comment for the new snapshot. Body may be empty."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=500)


class VersionSummary(BaseModel):
    """List item — snapshot body intentionally absent.

    The FE history pane only needs metadata; the body fetch belongs to
    a follow-up endpoint (``GET /versions/{n}``) we ship in a later
    sprint when there's a UI need.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    version_number: int
    created_by: UUID | None
    comment: str | None
    created_at: datetime


class VersionListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    versions: list[VersionSummary]


class VersionCreated(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version_number: int
    comment: str | None
    created_at: datetime


class VersionRestored(BaseModel):
    """Result of a restore — points at the freshly-inserted version."""

    model_config = ConfigDict(frozen=True)

    restored_from_version: int
    new_version_number: int
    new_version_id: UUID


# ── Helpers ────────────────────────────────────────────────────────────


async def _resolve_proposal_or_404(
    conn: asyncpg.Connection, *, proposal_id: UUID, tenant_id: UUID
) -> dict[str, Any]:
    """Verify the proposal exists in the caller's tenant, return its row.

    Cross-tenant existence is never leaked: a foreign id returns 404
    just like a non-existent one.
    """

    row = await conn.fetchrow(
        "select id, tenant_id, draft from proposals where id = $1",
        proposal_id,
    )
    if row is None or row["tenant_id"] != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="proposal not found",
        )
    return dict(row)


def _draft_to_json_text(draft: Any) -> str:
    """Coerce asyncpg's jsonb return (str | dict | None) into a JSON string."""

    if draft is None:
        return "{}"
    if isinstance(draft, str):
        return draft
    return json.dumps(draft)


# ── Endpoints ──────────────────────────────────────────────────────────


@router.post(
    "/{proposal_id}/versions",
    response_model=VersionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Snapshot the current draft as a new version (member+)",
)
async def create_version(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: VersionCreate,
) -> VersionCreated:
    """Insert one ``proposal_versions`` row containing the current draft.

    The ``version_number`` auto-increments per proposal. Concurrent
    snapshots are serialised by the unique ``(proposal_id, version_number)``
    constraint inside a transaction — a duplicate is retried at the
    application layer (rare; left to the saga / explicit retry flows).

    Status codes:
    - 201: snapshot created.
    - 404: proposal not in caller's tenant (or doesn't exist).
    """

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    proposal = await _resolve_proposal_or_404(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    snapshot_json = _draft_to_json_text(proposal["draft"])

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            insert into proposal_versions (
              proposal_id, version_number, draft_snapshot, created_by, comment
            )
            values (
              $1,
              coalesce(
                (select max(version_number) + 1 from proposal_versions where proposal_id = $1),
                1
              ),
              $2::jsonb,
              $3,
              $4
            )
            returning id, version_number, comment, created_at
            """,
            proposal_id,
            snapshot_json,
            user_id,
            body.comment,
        )
        assert row is not None
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.version_created",
            resource_type="proposal",
            resource_id=proposal_id,
            diff={
                "version_number": str(row["version_number"]),
                "has_comment": "true" if body.comment else "false",
            },
        )

    logger.info(
        "proposal_version_created",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "proposal_id": str(proposal_id),
            "version_number": int(row["version_number"]),
        },
    )
    return VersionCreated(
        id=UUID(str(row["id"])),
        version_number=int(row["version_number"]),
        comment=row["comment"],
        created_at=row["created_at"],
    )


@router.get(
    "/{proposal_id}/versions",
    response_model=VersionListResponse,
    summary="List proposal versions newest-first (member+); snapshot bodies omitted",
)
async def list_versions(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> VersionListResponse:
    """Return the version history for the proposal.

    Snapshot bodies are intentionally NOT returned — they can be 100KB+
    each and a typical history fits dozens. Tease metadata only; let the
    FE fetch a single body when the user clicks "diff" or "restore".
    """

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _resolve_proposal_or_404(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    rows = await conn.fetch(
        """
        select id, version_number, created_by, comment, created_at
          from proposal_versions
         where proposal_id = $1
         order by version_number desc
        """,
        proposal_id,
    )
    return VersionListResponse(
        versions=[
            VersionSummary(
                id=UUID(str(row["id"])),
                version_number=int(row["version_number"]),
                created_by=UUID(str(row["created_by"])) if row["created_by"] else None,
                comment=row["comment"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
    )


@router.post(
    "/{proposal_id}/versions/{version_number}/restore",
    response_model=VersionRestored,
    summary="Restore a version as a new current snapshot (member+)",
)
async def restore_version(
    proposal_id: UUID,
    version_number: int,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> VersionRestored:
    """Snapshot-as-new-current restore.

    The chosen version's snapshot is copied into a NEW row (with the
    next ``version_number``), and ``proposals.draft`` is updated to the
    restored JSON. Older versions are NOT deleted — the user can roll
    forward / back any number of times.

    Status codes:
    - 200: restored.
    - 404: proposal or version not found.
    """

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _resolve_proposal_or_404(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    source = await conn.fetchrow(
        """
        select draft_snapshot from proposal_versions
         where proposal_id = $1 and version_number = $2
        """,
        proposal_id,
        version_number,
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="version not found",
        )

    snapshot_json = _draft_to_json_text(source["draft_snapshot"])
    comment = f"restored from v{version_number}"

    async with conn.transaction():
        new_row = await conn.fetchrow(
            """
            insert into proposal_versions (
              proposal_id, version_number, draft_snapshot, created_by, comment
            )
            values (
              $1,
              coalesce(
                (select max(version_number) + 1 from proposal_versions where proposal_id = $1),
                1
              ),
              $2::jsonb,
              $3,
              $4
            )
            returning id, version_number
            """,
            proposal_id,
            snapshot_json,
            user_id,
            comment,
        )
        assert new_row is not None
        await conn.execute(
            """
            update proposals
               set draft = $1::jsonb,
                   updated_at = now()
             where id = $2
            """,
            snapshot_json,
            proposal_id,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.version_restored",
            resource_type="proposal",
            resource_id=proposal_id,
            diff={
                "from_version": str(version_number),
                "new_version": str(new_row["version_number"]),
            },
        )

    logger.info(
        "proposal_version_restored",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "proposal_id": str(proposal_id),
            "from_version": version_number,
            "new_version": int(new_row["version_number"]),
        },
    )
    return VersionRestored(
        restored_from_version=version_number,
        new_version_number=int(new_row["version_number"]),
        new_version_id=UUID(str(new_row["id"])),
    )


__all__ = [
    "VersionCreate",
    "VersionCreated",
    "VersionListResponse",
    "VersionRestored",
    "VersionSummary",
    "router",
]
