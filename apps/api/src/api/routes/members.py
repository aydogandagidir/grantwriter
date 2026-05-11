"""Owner-side member management — list, role change, remove.

All routes are owner/admin-only and tenant-scoped. They mirror the
operations a tenant administrator runs from the Settings → Team page:

- ``GET    /api/v1/tenant/members``         — list active members
- ``PATCH  /api/v1/tenant/members/{id}/role`` — promote/demote
- ``DELETE /api/v1/tenant/members/{id}``    — soft-remove

Sole-owner guard reuses :func:`src.core.tenant.count_active_owners`,
the same helper that protects the user-side ``DELETE /me/account``.

Self-modification is intentionally NOT allowed via these routes:

- Self role change → confusing UX (an owner accidentally demoting
  themselves and locking out admin features). Owners should ask
  another owner to demote them, or transfer ownership first.
- Self deletion → use ``DELETE /api/v1/me/account``, which has the
  KVKK-compliant audit + grace window flow.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import (
    count_active_owners,
    require_admin,
    resolve_tenant_and_role,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tenant/members", tags=["members"])


_ASSIGNABLE_ROLES = ("owner", "admin", "member", "viewer")


# ── Models ─────────────────────────────────────────────────────────────


class MemberSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str | None
    display_name: str | None
    role: str
    created_at: datetime


class MemberListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    members: list[MemberSummary]


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "admin", "member", "viewer"]


# ── Helpers ────────────────────────────────────────────────────────────


async def _load_member(
    conn: asyncpg.Connection, *, tenant_id: UUID, user_id: UUID
) -> asyncpg.Record:
    """Fetch a member row scoped to the tenant or 404."""

    row = await conn.fetchrow(
        """
        select u.id, u.role, u.tenant_id, u.deleted_at,
               u.display_name, u.created_at,
               au.email
          from public.users u
          left join auth.users au on au.id = u.id
         where u.id = $1 and u.tenant_id = $2
        """,
        user_id,
        tenant_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="member not found in this tenant",
        )
    return row


def _reject_self_modification(
    *, caller_id: UUID, target_id: UUID, action: str
) -> None:
    if caller_id == target_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"cannot {action} yourself via this endpoint — "
                "use /api/v1/me/account or ask another admin"
            ),
        )


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=MemberListResponse,
    summary="List active members of the caller's tenant (owner/admin only)",
)
async def list_members(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> MemberListResponse:
    """Return active (non-soft-deleted) members of the caller's tenant.

    Soft-deleted users are filtered out — the FE shouldn't see ghosts.
    The hard-delete grace task (Step 3) eventually purges them entirely.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="member list")

    rows = await conn.fetch(
        """
        select u.id, u.role, u.display_name, u.created_at, au.email
          from public.users u
          left join auth.users au on au.id = u.id
         where u.tenant_id = $1 and u.deleted_at is null
         order by case u.role
           when 'owner'  then 0
           when 'admin'  then 1
           when 'member' then 2
           when 'viewer' then 3
           else 4
         end, u.created_at asc
        """,
        tenant_id,
    )
    return MemberListResponse(
        members=[
            MemberSummary(
                id=UUID(str(row["id"])),
                email=row["email"],
                display_name=row["display_name"],
                role=str(row["role"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
    )


@router.patch(
    "/{member_id}/role",
    response_model=MemberSummary,
    summary="Change a member's role (owner/admin only)",
)
async def update_member_role(
    member_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: RoleUpdate,
) -> MemberSummary:
    """Promote or demote ``member_id`` to ``body.role``.

    Status codes:
    - 200: updated; returns the new member summary.
    - 400: caller targeted themselves (use ``/me/account`` or another admin).
    - 403: caller is not owner/admin.
    - 404: member not in this tenant.
    - 409: would leave the tenant without an active owner.
    """

    tenant_id, caller_role = await resolve_tenant_and_role(
        conn, user_id=user_id
    )
    require_admin(caller_role, action="member role change")
    _reject_self_modification(
        caller_id=user_id, target_id=member_id, action="change the role of"
    )

    member = await _load_member(
        conn, tenant_id=tenant_id, user_id=member_id
    )
    if member["deleted_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="member has been removed",
        )

    old_role = str(member["role"])
    new_role = body.role

    if old_role == new_role:
        # No-op — return the current state without writing an audit row.
        return MemberSummary(
            id=member_id,
            email=member["email"],
            display_name=member["display_name"],
            role=old_role,
            created_at=member["created_at"],
        )

    # Sole-owner guard: downgrading the only owner orphans the tenant.
    if old_role == "owner" and new_role != "owner":
        owners = await count_active_owners(conn, tenant_id=tenant_id)
        if owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "cannot demote the tenant's sole owner — "
                    "promote another member to owner first"
                ),
            )

    async with conn.transaction():
        await conn.execute(
            """
            update public.users
               set role = $1, updated_at = now()
             where id = $2 and tenant_id = $3
            """,
            new_role,
            member_id,
            tenant_id,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.member_role_changed",
            resource_type="user",
            resource_id=member_id,
            diff={"from": old_role, "to": new_role},
        )

    logger.info(
        "member_role_changed",
        extra={
            "tenant_id": str(tenant_id),
            "actor_user_id": str(user_id),
            "target_user_id": str(member_id),
            "from": old_role,
            "to": new_role,
        },
    )
    return MemberSummary(
        id=member_id,
        email=member["email"],
        display_name=member["display_name"],
        role=new_role,
        created_at=member["created_at"],
    )


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-remove a member (owner/admin only)",
)
async def remove_member(
    member_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> Response:
    """Soft-delete ``member_id`` from the caller's tenant.

    Status codes:
    - 204: removed (idempotent — also returns 204 if already removed).
    - 400: caller targeted themselves (use ``/me/account``).
    - 403: caller is not owner/admin.
    - 404: member not in this tenant.
    - 409: member is the tenant's sole active owner.
    """

    tenant_id, caller_role = await resolve_tenant_and_role(
        conn, user_id=user_id
    )
    require_admin(caller_role, action="member removal")
    _reject_self_modification(
        caller_id=user_id, target_id=member_id, action="remove"
    )

    member = await _load_member(
        conn, tenant_id=tenant_id, user_id=member_id
    )

    # Idempotent: already removed → 204, no audit re-write.
    if member["deleted_at"] is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if str(member["role"]) == "owner":
        owners = await count_active_owners(conn, tenant_id=tenant_id)
        if owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "cannot remove the tenant's sole owner — "
                    "promote another member to owner first"
                ),
            )

    async with conn.transaction():
        await conn.execute(
            "update public.users set deleted_at = now() where id = $1",
            member_id,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.member_removed",
            resource_type="user",
            resource_id=member_id,
            diff={"role": str(member["role"]), "event": "soft_removed"},
        )

    logger.info(
        "member_removed",
        extra={
            "tenant_id": str(tenant_id),
            "actor_user_id": str(user_id),
            "target_user_id": str(member_id),
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "MemberListResponse",
    "MemberSummary",
    "RoleUpdate",
    "router",
]
