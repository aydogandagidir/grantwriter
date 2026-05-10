"""Tenant invitation flow — admin issues, public previews, invitee accepts.

Two routers, both mounted from this module:

- ``/api/v1/tenant/invitations`` — admin/owner CRUD over their tenant's
  pending invitations. Every mutation writes one ``audit_log`` event.
- ``/api/v1/invitations`` — invitee-facing. ``GET /{token}`` is **public**
  (the invitee may not have an account yet) and returns just enough
  context to render the accept page (tenant name, inviter, role,
  expiry). ``POST /accept`` requires the invitee's JWT and links them
  to the tenant.

Token surface — the secret only flows out once:

- ``POST`` returns the token (the FE / admin emails it manually until
  the email service lands).
- ``GET /tenant/invitations`` and ``GET /tenant/invitations/{id}`` (not
  exposed) deliberately omit the token — same model as API keys.

Edge cases:

- Token expired / already accepted → 410 Gone.
- Email already in ``auth.users`` (account exists) → 409 Conflict.
- Pending invitation already exists for this email → 409 Conflict
  (delete the existing one first).
- ``public.users`` PK is the auth user id, so a single user belongs to
  exactly one tenant — accept is rejected with 409 if the invitee is
  already a member somewhere.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import require_admin, resolve_tenant_and_role

logger = logging.getLogger(__name__)

# Two routers — different prefixes, same module so the lookup helpers stay shared.
tenant_router = APIRouter(
    prefix="/api/v1/tenant/invitations", tags=["invitations"]
)
public_router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])


_ALLOWED_INVITE_ROLES = ("member", "admin")
_TOKEN_BYTES = 32


# ── Models ─────────────────────────────────────────────────────────────


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["member", "admin"] = "member"


class InvitationCreated(BaseModel):
    """POST response — the only place the token ever appears."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    role: str
    token: str
    expires_at: datetime
    accept_url_path: str = Field(
        description=(
            "Server-relative path the FE renders into a clickable link. "
            "Compose with the FE origin: ``${origin}${accept_url_path}``."
        ),
    )


class InvitationSummary(BaseModel):
    """List item — token deliberately omitted (write-only secret)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    role: str
    invited_by: UUID | None
    expires_at: datetime
    created_at: datetime


class InvitationListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    invitations: list[InvitationSummary]


class InvitationPreview(BaseModel):
    """Public preview shown to the invitee BEFORE they accept."""

    model_config = ConfigDict(frozen=True)

    tenant_name: str
    tenant_slug: str
    role: str
    invited_email: str
    inviter_display_name: str | None
    expires_at: datetime


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=128)


class InvitationAccepted(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    role: str


# ── Helpers ────────────────────────────────────────────────────────────


def _gone(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def _email_already_in_a_tenant(
    conn: asyncpg.Connection, *, email: str
) -> bool:
    """True iff the email already belongs to an active tenant member.

    A Supabase account WITHOUT a ``public.users`` row is fine — that's
    the typical invite flow (sign-up first, then accept). We only reject
    when the user is already in a tenant; the ``public.users`` PK
    constraint would otherwise reject the accept anyway.
    """

    return bool(
        await conn.fetchval(
            """
            select exists (
              select 1 from public.users u
                join auth.users au on au.id = u.id
               where au.email = $1
                 and u.deleted_at is null
            )
            """,
            email,
        )
    )


async def _email_already_pending(
    conn: asyncpg.Connection, *, tenant_id: UUID, email: str
) -> bool:
    return bool(
        await conn.fetchval(
            """
            select exists (
              select 1 from tenant_invitations
               where tenant_id = $1
                 and email = $2
                 and accepted_at is null
                 and expires_at > now()
            )
            """,
            tenant_id,
            email,
        )
    )


async def _user_already_in_a_tenant(
    conn: asyncpg.Connection, *, user_id: UUID
) -> bool:
    return bool(
        await conn.fetchval(
            "select exists (select 1 from public.users where id = $1)",
            user_id,
        )
    )


# ── Tenant router (admin/owner) ────────────────────────────────────────


@tenant_router.post(
    "",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new invitation (owner/admin only) — token returned ONCE",
)
async def create_invitation(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: InvitationCreate,
) -> InvitationCreated:
    """Create a new ``tenant_invitations`` row + return the bearer token.

    The token is the only piece of secret material returned by the API,
    and only here. Subsequent reads (list/get) deliberately omit it.

    Status codes:
    - 201: created.
    - 403: caller is not owner/admin.
    - 409: email already has an account, or another pending invite for
      this email exists for the same tenant.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="invite")

    target_email = body.email.lower()

    if await _email_already_in_a_tenant(conn, email=target_email):
        raise _conflict("that email already belongs to an active tenant member")

    if await _email_already_pending(
        conn, tenant_id=tenant_id, email=target_email
    ):
        raise _conflict(
            "an active invitation for this email already exists; "
            "delete it before issuing a new one"
        )

    token = _new_token()

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            insert into tenant_invitations (
              tenant_id, email, role, invited_by, token
            ) values ($1, $2, $3, $4, $5)
            returning id, email, role, expires_at
            """,
            tenant_id,
            target_email,
            body.role,
            user_id,
            token,
        )
        assert row is not None
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.invitation_sent",
            resource_type="tenant_invitation",
            resource_id=UUID(str(row["id"])),
            diff={"role": body.role},
        )

    logger.info(
        "invitation_created",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "invitation_id": str(row["id"]),
            "role": body.role,
        },
    )
    return InvitationCreated(
        id=UUID(str(row["id"])),
        email=str(row["email"]),
        role=str(row["role"]),
        token=token,
        expires_at=row["expires_at"],
        accept_url_path=f"/invitations/{token}",
    )


@tenant_router.get(
    "",
    response_model=InvitationListResponse,
    summary="List the tenant's pending invitations (owner/admin only)",
)
async def list_invitations(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> InvitationListResponse:
    """Return non-expired, non-accepted invitations.

    ``token`` is intentionally NOT returned — it's a single-use secret
    that only flows out of POST. If an admin lost it, they revoke and
    re-issue.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="invite list")

    rows = await conn.fetch(
        """
        select id, email, role, invited_by, expires_at, created_at
          from tenant_invitations
         where tenant_id = $1
           and accepted_at is null
           and expires_at > now()
         order by created_at desc
        """,
        tenant_id,
    )
    return InvitationListResponse(
        invitations=[
            InvitationSummary(
                id=UUID(str(row["id"])),
                email=str(row["email"]),
                role=str(row["role"]),
                invited_by=UUID(str(row["invited_by"]))
                if row["invited_by"]
                else None,
                expires_at=row["expires_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
    )


@tenant_router.delete(
    "/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation (owner/admin only)",
)
async def revoke_invitation(
    invitation_id: UUID,
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> Response:
    """Hard-delete the invitation — once revoked, the token is dead.

    Status codes:
    - 204: revoked (idempotent — also returns 204 if already gone).
    - 403: caller is not owner/admin.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="invite revoke")

    async with conn.transaction():
        deleted = await conn.fetchval(
            """
            delete from tenant_invitations
             where id = $1 and tenant_id = $2
             returning email
            """,
            invitation_id,
            tenant_id,
        )
        if deleted is not None:
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="tenant.invitation_revoked",
                resource_type="tenant_invitation",
                resource_id=invitation_id,
                diff={"event": "revoked"},
            )

    logger.info(
        "invitation_revoked",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "invitation_id": str(invitation_id),
            "existed": deleted is not None,
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Public router (invitee) ────────────────────────────────────────────


@public_router.get(
    "/{token}",
    response_model=InvitationPreview,
    summary="Preview an invitation by token (public — no JWT)",
)
async def preview_invitation(
    token: str,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> InvitationPreview:
    """Return the human-readable context the invitee needs before accepting.

    Status codes:
    - 200: preview body.
    - 404: token doesn't match any invitation.
    - 410: token expired or invitation already accepted.
    """

    row = await conn.fetchrow(
        """
        select i.email, i.role, i.expires_at, i.accepted_at,
               t.name as tenant_name, t.slug as tenant_slug,
               iu.display_name as inviter_display_name
          from tenant_invitations i
          join tenants t on t.id = i.tenant_id
          left join public.users iu on iu.id = i.invited_by
         where i.token = $1
        """,
        token,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found"
        )
    if row["accepted_at"] is not None:
        raise _gone("invitation already accepted")
    if row["expires_at"] <= datetime.now(row["expires_at"].tzinfo):
        raise _gone("invitation expired")

    return InvitationPreview(
        tenant_name=str(row["tenant_name"]),
        tenant_slug=str(row["tenant_slug"]),
        role=str(row["role"]),
        invited_email=str(row["email"]),
        inviter_display_name=row["inviter_display_name"],
        expires_at=row["expires_at"],
    )


@public_router.post(
    "/accept",
    response_model=InvitationAccepted,
    summary="Accept an invitation — links the JWT caller to the tenant",
)
async def accept_invitation(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: InvitationAccept,
) -> InvitationAccepted:
    """Validate the token + caller's email, then create the membership.

    Status codes:
    - 200: accepted, returns the new ``(tenant_id, role)``.
    - 403: caller's email does not match the invitation email.
    - 404: invalid token.
    - 409: caller already belongs to a tenant.
    - 410: invitation expired or already accepted.
    """

    invitation = await conn.fetchrow(
        """
        select id, tenant_id, email, role, expires_at, accepted_at
          from tenant_invitations
         where token = $1
        """,
        body.token,
    )
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found"
        )
    if invitation["accepted_at"] is not None:
        raise _gone("invitation already accepted")
    if invitation["expires_at"] <= datetime.now(
        invitation["expires_at"].tzinfo
    ):
        raise _gone("invitation expired")

    caller_email = await conn.fetchval(
        "select email from auth.users where id = $1", user_id
    )
    if caller_email is None:
        # JWT validated against Supabase but the user row is gone — rare,
        # treat as 403 (auth identity mismatch) rather than 500.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auth user not found",
        )
    if str(caller_email).lower() != str(invitation["email"]).lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invitation email does not match the authenticated user",
        )

    if await _user_already_in_a_tenant(conn, user_id=user_id):
        raise _conflict("user already belongs to a tenant")

    tenant_id = UUID(str(invitation["tenant_id"]))
    role = str(invitation["role"])

    async with conn.transaction():
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role)
            values ($1, $2, $3)
            """,
            user_id,
            tenant_id,
            role,
        )
        await conn.execute(
            "update tenant_invitations set accepted_at = now() where id = $1",
            UUID(str(invitation["id"])),
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.invitation_accepted",
            resource_type="tenant_invitation",
            resource_id=UUID(str(invitation["id"])),
            diff={"role": role},
        )

    logger.info(
        "invitation_accepted",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "invitation_id": str(invitation["id"]),
            "role": role,
        },
    )
    return InvitationAccepted(tenant_id=tenant_id, role=role)


__all__ = [
    "InvitationAccept",
    "InvitationAccepted",
    "InvitationCreate",
    "InvitationCreated",
    "InvitationListResponse",
    "InvitationPreview",
    "InvitationSummary",
    "public_router",
    "tenant_router",
]
