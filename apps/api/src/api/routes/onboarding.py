"""Self-service onboarding — tenant provisioning for new sign-ups.

Sprint 4 Day 16: When a Supabase auth user exists but has no
``public.users`` row (post-signup, pre-onboarding), the frontend
redirects to ``/onboarding``.  This endpoint lets the user create a
new tenant + user row in a single atomic transaction.

Single endpoint:

- ``POST /api/v1/onboarding`` — Create a new tenant and register the
  caller as its ``owner``.  Body: ``{name, slug, preferred_language?}``.

Guards:
  - The caller must have a valid Supabase JWT (i.e. ``auth.users`` row).
  - If the caller already has a ``public.users`` row → 409 Conflict.
  - Slug uniqueness is enforced by the DB unique constraint.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$")


# ── Models ─────────────────────────────────────────────────────────────


class OnboardingRequest(BaseModel):
    """``POST /api/v1/onboarding`` body."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100, description="Workspace display name")
    slug: str = Field(min_length=3, max_length=50, description="URL-safe workspace slug")
    preferred_language: str = Field(
        default="tr",
        description="User's preferred language (tr or en)",
    )

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        v = v.lower().strip()
        if not _SLUG_PATTERN.match(v):
            raise ValueError(
                "Slug must be 3-50 characters, lowercase alphanumeric and hyphens, "
                "starting and ending with a letter or digit."
            )
        return v

    @field_validator("preferred_language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in ("tr", "en"):
            raise ValueError("preferred_language must be 'tr' or 'en'")
        return v


class OnboardingResponse(BaseModel):
    """Returned after successful onboarding."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    tenant_name: str
    tenant_slug: str
    user_id: UUID
    role: str


# ── Route ──────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=OnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace (tenant) and register the caller as owner",
)
async def create_workspace(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: OnboardingRequest,
) -> OnboardingResponse:
    """Provision a new tenant and ``public.users`` row atomically.

    This is the self-service onboarding path for users who signed up via
    Supabase Auth but have not yet been assigned to a tenant (no invite).

    Guards:
    - 409 if the caller already has a ``public.users`` row.
    - 409 if the slug is already taken (DB unique constraint).
    """

    # Check if the user already has a public.users row
    existing = await conn.fetchval(
        "select id from public.users where id = $1",
        user_id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user already belongs to a workspace",
        )

    # Fetch display name / email from auth.users for the user row
    auth_row = await conn.fetchrow(
        "select email, raw_user_meta_data from auth.users where id = $1",
        user_id,
    )
    if auth_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="auth user not found",
        )

    display_name: str | None = None
    if auth_row["raw_user_meta_data"]:
        meta = auth_row["raw_user_meta_data"]
        if isinstance(meta, dict):
            display_name = meta.get("display_name") or meta.get("full_name")

    try:
        async with conn.transaction():
            # 1. Create the tenant
            tenant_row = await conn.fetchrow(
                """
                insert into tenants (name, slug)
                values ($1, $2)
                returning id, name, slug
                """,
                body.name,
                body.slug,
            )
            assert tenant_row is not None
            tenant_id = UUID(str(tenant_row["id"]))

            # 2. Create the public.users row as owner
            await conn.execute(
                """
                insert into public.users (id, tenant_id, role, display_name, preferred_language)
                values ($1, $2, 'owner', $3, $4)
                """,
                user_id,
                tenant_id,
                display_name,
                body.preferred_language,
            )

            # 3. Audit event
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="tenant.created",
                resource_type="tenant",
                resource_id=tenant_id,
                diff={
                    "name": body.name,
                    "slug": body.slug,
                    "plan": "starter",
                },
            )
    except asyncpg.UniqueViolationError as exc:
        constraint = getattr(exc, "constraint_name", "") or ""
        if "slug" in constraint or "tenants_slug" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="this workspace slug is already taken",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="workspace creation conflict",
        ) from exc

    logger.info(
        "workspace_created",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "slug": body.slug,
        },
    )

    return OnboardingResponse(
        tenant_id=tenant_id,
        tenant_name=str(tenant_row["name"]),
        tenant_slug=str(tenant_row["slug"]),
        user_id=user_id,
        role="owner",
    )


__all__ = [
    "OnboardingRequest",
    "OnboardingResponse",
    "router",
]
