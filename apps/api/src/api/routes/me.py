"""Self-service KVKK / GDPR endpoints.

Two routes the user invokes against their own data:

- ``GET  /api/v1/me/data-export``  — KVKK 11 / GDPR 20 (right to access /
  portability). Returns a JSON envelope with the user's identity row,
  their tenant summary, the audit and usage log entries that name them
  as the actor, and a list of proposals they authored (id + title +
  programme + status — full content is tenant-owned and out of scope
  here).
- ``DELETE /api/v1/me/account`` — KVKK 7 / GDPR 17 (right to erasure).
  Soft-deletes the user by setting ``public.users.deleted_at = now()``;
  the :func:`auth.tenant_id` SECURITY DEFINER helper already filters on
  ``deleted_at is null``, so a deleted user immediately becomes invisible
  to every RLS policy. A nightly Celery beat task (Faz 2) does the
  actual hard delete after the 30-day grace window.

**Sole-owner guard.** If deleting the user would leave their tenant
without an active owner, the route returns ``409 Conflict`` so the FE
can show "transfer ownership before deleting your account". Letting an
ownerless tenant exist would orphan billing + audit + proposals.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import count_active_owners

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["me"])


# ── Response models ────────────────────────────────────────────────────


class UserSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str | None
    display_name: str | None
    preferred_language: str
    role: str
    created_at: datetime


class TenantSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    slug: str
    plan: str


class AuditEventSummary(BaseModel):
    """One audit row authored by the user — minimal projection."""

    model_config = ConfigDict(frozen=True)

    action: str
    resource_type: str | None
    resource_id: UUID | None
    created_at: datetime


class UsageEventSummary(BaseModel):
    """One LLM call attributable to the user."""

    model_config = ConfigDict(frozen=True)

    event_type: str
    resource: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    used_byok: bool | None
    created_at: datetime


class ProposalSummary(BaseModel):
    """Proposal authored by the user — id + classification, no content."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    programme_id: str
    language: str
    status: str
    title: str | None
    created_at: datetime


class UserDataExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    exported_at: datetime
    user: UserSummary
    tenant: TenantSummary
    audit_events: list[AuditEventSummary]
    usage_log: list[UsageEventSummary]
    proposals_authored: list[ProposalSummary]


class MeResponse(BaseModel):
    """Identity + tenant snapshot for the calling user.

    Consumed by the FE's authenticated app shell (``(app)/layout.tsx``)
    to decide between rendering the dashboard and redirecting to
    ``/onboarding``. The shape mirrors
    ``packages/shared-types/src/index.ts::MeResponse`` — keep both halves
    in sync when adding fields.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str | None
    display_name: str | None
    role: str
    tenant_id: UUID
    tenant_name: str
    tenant_slug: str
    plan: str


# ── Helpers ────────────────────────────────────────────────────────────


async def _load_user(
    conn: asyncpg.Connection, *, user_id: UUID
) -> tuple[UserSummary, TenantSummary]:
    """Load the user + their tenant; 404 if soft-deleted or unknown."""

    row = await conn.fetchrow(
        """
        select u.id, u.role, u.preferred_language, u.display_name,
               u.created_at,
               au.email,
               t.id   as tenant_id,
               t.name as tenant_name,
               t.slug as tenant_slug,
               t.plan as tenant_plan
          from public.users u
          join tenants t on t.id = u.tenant_id
          left join auth.users au on au.id = u.id
         where u.id = $1 and u.deleted_at is null
        """,
        user_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found or has been deleted",
        )
    user = UserSummary(
        id=UUID(str(row["id"])),
        email=row["email"],
        display_name=row["display_name"],
        preferred_language=str(row["preferred_language"] or "tr"),
        role=str(row["role"]),
        created_at=row["created_at"],
    )
    tenant = TenantSummary(
        id=UUID(str(row["tenant_id"])),
        name=str(row["tenant_name"]),
        slug=str(row["tenant_slug"]),
        plan=str(row["tenant_plan"]),
    )
    return user, tenant


async def _load_audit_events(
    conn: asyncpg.Connection, *, user_id: UUID
) -> list[AuditEventSummary]:
    rows = await conn.fetch(
        """
        select action, resource_type, resource_id, created_at
          from audit_log
         where user_id = $1
         order by created_at desc
         limit 500
        """,
        user_id,
    )
    return [
        AuditEventSummary(
            action=str(row["action"]),
            resource_type=row["resource_type"],
            resource_id=UUID(str(row["resource_id"])) if row["resource_id"] else None,
            created_at=row["created_at"],
        )
        for row in rows
    ]


async def _load_usage_log(
    conn: asyncpg.Connection, *, user_id: UUID
) -> list[UsageEventSummary]:
    rows = await conn.fetch(
        """
        select event_type, resource, input_tokens, output_tokens,
               cost_usd, used_byok, created_at
          from tenant_usage_log
         where user_id = $1
         order by created_at desc
         limit 500
        """,
        user_id,
    )
    return [
        UsageEventSummary(
            event_type=str(row["event_type"]),
            resource=row["resource"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
            used_byok=row["used_byok"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


async def _load_proposals(
    conn: asyncpg.Connection, *, user_id: UUID
) -> list[ProposalSummary]:
    rows = await conn.fetch(
        """
        select id, programme_id, language, status, title, created_at
          from proposals
         where created_by = $1
         order by created_at desc
        """,
        user_id,
    )
    return [
        ProposalSummary(
            id=UUID(str(row["id"])),
            programme_id=str(row["programme_id"]),
            language=str(row["language"]),
            status=str(row["status"]),
            title=row["title"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=MeResponse,
    summary="Identity + tenant snapshot for the calling user",
)
async def get_me(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> MeResponse:
    """Return the caller's user + tenant projection.

    Used by the FE app shell to decide whether to render the dashboard
    or redirect to onboarding. Returns 404 (caught by the FE layout) if
    the Supabase auth user has no corresponding ``public.users`` row —
    that's how the layout knows to redirect a fresh signup to the
    onboarding wizard.
    """

    user, tenant = await _load_user(conn, user_id=user_id)
    return MeResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        plan=tenant.plan,
    )


@router.get(
    "/data-export",
    response_model=UserDataExport,
    summary="Export the caller's personal data (KVKK 11 / GDPR 20)",
)
async def export_my_data(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> UserDataExport:
    """Return a JSON envelope of every record that names the caller.

    - 200: the export envelope.
    - 404: the user does not exist or has been soft-deleted.

    Proposal content is intentionally excluded — the proposals belong to
    the tenant, not the individual user. The IDs + classification are
    enough for the user to know what they wrote; full export of proposal
    bodies happens via the tenant-level export tool (Faz 2).
    """

    user, tenant = await _load_user(conn, user_id=user_id)
    audit_events = await _load_audit_events(conn, user_id=user_id)
    usage_log = await _load_usage_log(conn, user_id=user_id)
    proposals = await _load_proposals(conn, user_id=user_id)

    logger.info(
        "user_data_exported",
        extra={
            "user_id": str(user_id),
            "tenant_id": str(tenant.id),
            "audit_count": len(audit_events),
            "usage_count": len(usage_log),
            "proposal_count": len(proposals),
        },
    )
    return UserDataExport(
        exported_at=datetime.utcnow(),
        user=user,
        tenant=tenant,
        audit_events=audit_events,
        usage_log=usage_log,
        proposals_authored=proposals,
    )


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete the caller's account (KVKK 7 / GDPR 17)",
)
async def delete_my_account(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> Response:
    """Set ``public.users.deleted_at = now()`` for the caller.

    The auth helpers (``auth.tenant_id``, ``resolve_tenant_and_role``)
    already filter on ``deleted_at is null``, so the user immediately
    loses access to every RLS-scoped resource. A nightly Celery task
    (Faz 2) hard-deletes after the 30-day grace window.

    Status codes:
    - 204: success or already deleted (idempotent).
    - 409: caller is the sole active owner of their tenant — must
      transfer ownership first to avoid orphaning the tenant's data.
    """

    row = await conn.fetchrow(
        """
        select tenant_id, role, deleted_at
          from public.users where id = $1
        """,
        user_id,
    )
    if row is None:
        # Never existed — return 204 to avoid revealing whether the
        # user_id is registered. (Not strictly required since they came
        # in with a valid JWT, but keeps the surface uniform.)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Idempotent: already deleted → 204, no audit re-write.
    if row["deleted_at"] is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    tenant_id = UUID(str(row["tenant_id"]))
    role = str(row["role"])

    if role == "owner":
        active_owners = await count_active_owners(conn, tenant_id=tenant_id)
        if active_owners <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "cannot delete the tenant's sole owner — "
                    "transfer ownership to another member first"
                ),
            )

    async with conn.transaction():
        await conn.execute(
            "update public.users set deleted_at = now() where id = $1",
            user_id,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="user.account_deleted",
            resource_type="user",
            resource_id=user_id,
            diff={"event": "soft_deleted", "role": role},
        )

    logger.info(
        "user_account_soft_deleted",
        extra={
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": role,
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "AuditEventSummary",
    "MeResponse",
    "ProposalSummary",
    "TenantSummary",
    "UsageEventSummary",
    "UserDataExport",
    "UserSummary",
    "router",
]
