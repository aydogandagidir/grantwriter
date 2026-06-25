"""Post-signup workspace bootstrap.

A Supabase user lands here right after email confirmation: the JWT is
valid, but they don't have a ``public.users`` row yet. The FE's
onboarding wizard collects a workspace name + locale, then POSTs to
``/api/v1/onboarding/workspace`` to create the tenant + link the
caller as its first owner.

Why a dedicated endpoint and not a generic ``POST /tenants``?

- We're the only insert point for new tenants — non-invitee flows go
  through here exclusively. The endpoint can therefore enforce the
  "one workspace per signup" invariant in app code without a generic
  CRUD surface tempting future callers to bypass it.
- The audit row stamps ``tenant.created`` with the source so an
  operator scanning the audit log can spot anomalies (one user
  bootstrapping ten workspaces).

All paid-plan onboarding flows defer to Iyzico checkout — this route
always provisions on the **starter** plan. Upgrades land via the
billing endpoints + Iyzico webhook.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


# Slug rule: lowercase letters / digits / single hyphens, 3-40 chars.
# Matches what tenants.slug column tolerates AND keeps URLs clean.
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SLUG_MIN_LEN = 3
_SLUG_MAX_LEN = 40

# Starter plan defaults — kept in sync with src/billing/plan_mapping.py.
_STARTER_PLAN = "starter"
_STARTER_MONTHLY_PROPOSAL_LIMIT = 3


# ── Models ─────────────────────────────────────────────────────────────


class WorkspaceCreateRequest(BaseModel):
    """Wizard step 1 body.

    ``slug`` is optional — when absent we derive it from the name. The
    server-side slug normalisation guarantees URL-safety and avoids
    putting that burden on the FE; the FE can still validate locally
    for instant feedback.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    slug: str | None = Field(default=None, min_length=_SLUG_MIN_LEN, max_length=_SLUG_MAX_LEN)
    preferred_language: Literal["tr", "en"] = "tr"


class WorkspaceCreatedResponse(BaseModel):
    """``201`` body — FE redirects to ``/dashboard`` after this."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    slug: str
    role: Literal["owner"]
    plan: str


# ── Helpers ────────────────────────────────────────────────────────────


def _derive_slug(name: str) -> str:
    """Normalise a free-text name to a URL-safe slug.

    Lowercases, collapses non-alphanumeric runs into single hyphens,
    trims hyphens at the edges, and tail-suffixes a uuid4 fragment when
    the result would be too short (e.g. all-emoji names). The collision
    case is handled separately by the caller — derive_slug only fixes
    *shape*, not *uniqueness*.
    """

    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(cleaned) < _SLUG_MIN_LEN:
        cleaned = f"workspace-{uuid4().hex[:8]}"
    return cleaned[:_SLUG_MAX_LEN]


async def _user_already_in_a_tenant(
    conn: asyncpg.Connection, *, user_id: UUID
) -> bool:
    """True iff caller has a ``public.users`` row (active or deleted).

    A soft-deleted row still occupies the PK — letting the caller
    create a new tenant would either crash on the unique constraint or
    silently turn into a re-activation. Either way: reject.
    """

    return bool(
        await conn.fetchval(
            "select exists (select 1 from public.users where id = $1)",
            user_id,
        )
    )


# ── Route ──────────────────────────────────────────────────────────────


@router.post(
    "/workspace",
    response_model=WorkspaceCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the caller's first workspace + link them as owner",
)
async def create_workspace(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: WorkspaceCreateRequest,
) -> WorkspaceCreatedResponse:
    """One-shot bootstrap: insert tenant, link owner, audit.

    Status codes:
    - 201: created — body carries the new tenant id + slug + plan.
    - 400: requested slug failed validation.
    - 403: auth.users row is missing (rare — Supabase user deleted
      between login and POST).
    - 409: caller already belongs to a tenant, or slug is taken.
    """

    caller_email = await conn.fetchval(
        "select email from auth.users where id = $1", user_id
    )
    if caller_email is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auth user not found",
        )

    if await _user_already_in_a_tenant(conn, user_id=user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user already belongs to a tenant",
        )

    requested_slug = body.slug or _derive_slug(body.name)
    if not _SLUG_PATTERN.fullmatch(requested_slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "slug must be lowercase letters / digits / single hyphens "
                f"({_SLUG_MIN_LEN}-{_SLUG_MAX_LEN} chars)"
            ),
        )

    if await conn.fetchval(
        "select exists (select 1 from tenants where slug = $1)", requested_slug
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="slug already taken",
        )

    tenant_id = uuid4()
    async with conn.transaction():
        await conn.execute(
            """
            insert into tenants (
              id, name, slug, plan,
              monthly_proposal_limit, monthly_proposals_used
            ) values ($1, $2, $3, $4, $5, 0)
            """,
            tenant_id,
            body.name,
            requested_slug,
            _STARTER_PLAN,
            _STARTER_MONTHLY_PROPOSAL_LIMIT,
        )
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role, preferred_language)
            values ($1, $2, 'owner', $3)
            """,
            user_id,
            tenant_id,
            body.preferred_language,
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.created",
            resource_type="tenant",
            resource_id=tenant_id,
            diff={"source": "onboarding", "plan": _STARTER_PLAN},
        )

    logger.info(
        "workspace_created",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "slug": requested_slug,
            "plan": _STARTER_PLAN,
        },
    )
    return WorkspaceCreatedResponse(
        tenant_id=tenant_id,
        slug=requested_slug,
        role="owner",
        plan=_STARTER_PLAN,
    )


__all__ = ["WorkspaceCreateRequest", "WorkspaceCreatedResponse", "router"]
