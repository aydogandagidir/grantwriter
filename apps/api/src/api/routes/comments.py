"""Proposal comments — flat list with single-level threading.

Per docs/03 §2.3 + sprint-roadmap S3.D12. The ``proposal_comments``
table has been schema-ready since migration 006; this module wires it
to an API.

Five endpoints:

- ``POST   /api/v1/proposals/{id}/comments`` — add a comment. Body:
  ``{section?, anchor?, content, parent_id?}``. ``parent_id`` may not
  point at another reply (depth ≤ 1).
- ``GET    /api/v1/proposals/{id}/comments`` — flat list. Default hides
  resolved comments; ``?include_resolved=true`` returns everything.
- ``PATCH  /api/v1/comments/{id}`` — edit content (author only).
- ``POST   /api/v1/comments/{id}/resolve`` — mark resolved (author OR
  tenant admin/owner).
- ``DELETE /api/v1/comments/{id}`` — remove (author OR admin). Replies
  cascade-delete in the same transaction (the FK has no ON DELETE
  CASCADE, so we do it ourselves).

Cross-tenant guard: all comment lookups join ``proposal_comments``
with ``proposals`` and require the proposal's ``tenant_id`` to match
the caller's. A foreign id returns 404 (never 403).

Audit: ``proposal.comment_added`` + ``proposal.comment_resolved``
land in :mod:`src.core.audit`. Edits + deletes are not audited
deliberately — over-instrumenting comment edit churn would balloon
``audit_log`` rows for very low security value.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import ADMIN_ROLES, resolve_tenant_and_role

logger = logging.getLogger(__name__)

# Two routers — proposal-scoped (POST/GET) + comment-id-scoped
# (PATCH/resolve/DELETE). They live in the same module because the
# helpers are shared.
proposal_router = APIRouter(prefix="/api/v1/proposals", tags=["comments"])
comment_router = APIRouter(prefix="/api/v1/comments", tags=["comments"])


_MAX_CONTENT = 5000


# ── Models ─────────────────────────────────────────────────────────────


class CommentCreate(BaseModel):
    """``POST /proposals/{id}/comments`` body."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=_MAX_CONTENT)
    section: str | None = Field(default=None, max_length=64)
    anchor: str | None = Field(default=None, max_length=128)
    parent_id: UUID | None = None


class CommentEdit(BaseModel):
    """``PATCH /comments/{id}`` body — content only."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=_MAX_CONTENT)


class CommentItem(BaseModel):
    """One row in the GET response — flat, FE renders threading from parent_id."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    proposal_id: UUID
    author_id: UUID
    section: str | None
    anchor: str | None
    content: str
    resolved: bool
    parent_id: UUID | None
    created_at: datetime


class CommentListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    comments: list[CommentItem]


# ── Helpers ────────────────────────────────────────────────────────────


async def _verify_proposal_tenant(
    conn: asyncpg.Connection, *, proposal_id: UUID, tenant_id: UUID
) -> None:
    """Raise 404 if proposal is missing or in a different tenant."""

    found = await conn.fetchval(
        "select tenant_id from proposals where id = $1", proposal_id
    )
    if found is None or UUID(str(found)) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="proposal not found",
        )


async def _load_comment_for_tenant(
    conn: asyncpg.Connection, *, comment_id: UUID, tenant_id: UUID
) -> dict[str, Any]:
    """Fetch a comment, raising 404 if missing or cross-tenant."""

    row = await conn.fetchrow(
        """
        select c.id, c.proposal_id, c.author_id, c.section, c.anchor,
               c.content, c.resolved, c.parent_id, c.created_at,
               p.tenant_id
          from proposal_comments c
          join proposals p on p.id = c.proposal_id
         where c.id = $1
        """,
        comment_id,
    )
    if row is None or UUID(str(row["tenant_id"])) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )
    return dict(row)


async def _ensure_parent_eligible(
    conn: asyncpg.Connection, *, parent_id: UUID, proposal_id: UUID
) -> None:
    """Reject when ``parent_id`` is itself a reply (depth ≤ 1).

    Also requires the parent to belong to the same proposal — protects
    against cross-proposal threading bugs.
    """

    parent = await conn.fetchrow(
        "select proposal_id, parent_id from proposal_comments where id = $1",
        parent_id,
    )
    if parent is None or UUID(str(parent["proposal_id"])) != proposal_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="parent comment not found in this proposal",
        )
    if parent["parent_id"] is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="comments support only one level of replies",
        )


def _to_item(row: dict[str, Any]) -> CommentItem:
    return CommentItem(
        id=UUID(str(row["id"])),
        proposal_id=UUID(str(row["proposal_id"])),
        author_id=UUID(str(row["author_id"])),
        section=row["section"],
        anchor=row["anchor"],
        content=str(row["content"]),
        resolved=bool(row["resolved"]),
        parent_id=UUID(str(row["parent_id"])) if row["parent_id"] else None,
        created_at=row["created_at"],
    )


# ── Proposal-scoped endpoints ──────────────────────────────────────────


@proposal_router.post(
    "/{proposal_id}/comments",
    response_model=CommentItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a proposal (member+)",
)
async def create_comment(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: CommentCreate,
) -> CommentItem:
    """Insert a top-level comment or a single-level reply."""

    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _verify_proposal_tenant(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )
    if body.parent_id is not None:
        await _ensure_parent_eligible(
            conn, parent_id=body.parent_id, proposal_id=proposal_id
        )

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            insert into proposal_comments (
              proposal_id, author_id, section, anchor, content, parent_id
            ) values ($1, $2, $3, $4, $5, $6)
            returning id, proposal_id, author_id, section, anchor, content,
                      resolved, parent_id, created_at
            """,
            proposal_id,
            user_id,
            body.section,
            body.anchor,
            body.content,
            body.parent_id,
        )
        assert row is not None
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.comment_added",
            resource_type="proposal_comment",
            resource_id=UUID(str(row["id"])),
            diff={
                "section": body.section or "none",
                "is_reply": "true" if body.parent_id else "false",
            },
        )

    logger.info(
        "proposal_comment_added",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "proposal_id": str(proposal_id),
            "comment_id": str(row["id"]),
        },
    )
    return _to_item(dict(row))


@proposal_router.get(
    "/{proposal_id}/comments",
    response_model=CommentListResponse,
    summary="List comments for a proposal (member+); newest-first",
)
async def list_comments(
    proposal_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    include_resolved: Annotated[bool, Query()] = False,
) -> CommentListResponse:
    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    await _verify_proposal_tenant(
        conn, proposal_id=proposal_id, tenant_id=tenant_id
    )

    if include_resolved:
        rows = await conn.fetch(
            """
            select id, proposal_id, author_id, section, anchor, content,
                   resolved, parent_id, created_at
              from proposal_comments
             where proposal_id = $1
             order by created_at desc
            """,
            proposal_id,
        )
    else:
        rows = await conn.fetch(
            """
            select id, proposal_id, author_id, section, anchor, content,
                   resolved, parent_id, created_at
              from proposal_comments
             where proposal_id = $1 and coalesce(resolved, false) = false
             order by created_at desc
            """,
            proposal_id,
        )
    return CommentListResponse(comments=[_to_item(dict(r)) for r in rows])


# ── Comment-id-scoped endpoints ────────────────────────────────────────


@comment_router.patch(
    "/{comment_id}",
    response_model=CommentItem,
    summary="Edit a comment's content (author only)",
)
async def edit_comment(
    comment_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: CommentEdit,
) -> CommentItem:
    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    comment = await _load_comment_for_tenant(
        conn, comment_id=comment_id, tenant_id=tenant_id
    )
    if UUID(str(comment["author_id"])) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the author can edit this comment",
        )

    row = await conn.fetchrow(
        """
        update proposal_comments
           set content = $1
         where id = $2
        returning id, proposal_id, author_id, section, anchor, content,
                  resolved, parent_id, created_at
        """,
        body.content,
        comment_id,
    )
    assert row is not None
    return _to_item(dict(row))


@comment_router.post(
    "/{comment_id}/resolve",
    response_model=CommentItem,
    summary="Mark a comment resolved (author or tenant admin/owner)",
)
async def resolve_comment(
    comment_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> CommentItem:
    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    comment = await _load_comment_for_tenant(
        conn, comment_id=comment_id, tenant_id=tenant_id
    )
    is_author = UUID(str(comment["author_id"])) == user_id
    is_admin = role in ADMIN_ROLES
    if not (is_author or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the author or a tenant admin can resolve this",
        )

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            update proposal_comments
               set resolved = true
             where id = $1
            returning id, proposal_id, author_id, section, anchor, content,
                      resolved, parent_id, created_at
            """,
            comment_id,
        )
        assert row is not None
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.comment_resolved",
            resource_type="proposal_comment",
            resource_id=comment_id,
            diff={"event": "resolved"},
        )

    return _to_item(dict(row))


@comment_router.delete(
    "/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment (author or admin); replies cascade",
)
async def delete_comment(
    comment_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> Response:
    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    comment = await _load_comment_for_tenant(
        conn, comment_id=comment_id, tenant_id=tenant_id
    )
    is_author = UUID(str(comment["author_id"])) == user_id
    is_admin = role in ADMIN_ROLES
    if not (is_author or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the author or a tenant admin can delete this",
        )

    # The schema's parent_id FK has no ON DELETE CASCADE, so we delete
    # any replies first inside the same transaction. Single SQL cuts
    # the round-trip count.
    async with conn.transaction():
        await conn.execute(
            "delete from proposal_comments where parent_id = $1", comment_id
        )
        await conn.execute(
            "delete from proposal_comments where id = $1", comment_id
        )

    logger.info(
        "proposal_comment_deleted",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "comment_id": str(comment_id),
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "CommentCreate",
    "CommentEdit",
    "CommentItem",
    "CommentListResponse",
    "comment_router",
    "proposal_router",
]
