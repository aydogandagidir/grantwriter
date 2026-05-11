"""Identity → tenant + role resolution.

The production DB pool runs as service_role (bypasses RLS), so route
handlers enforce tenant scoping in app code by mapping the JWT's
``sub`` claim to ``public.users.tenant_id``. This helper is the
single source of truth for that mapping; routes that need to gate by
role (admin-only endpoints) read the second tuple element.

Mirrors the SQL of the ``auth.tenant_id()`` and ``auth.is_tenant_admin()``
SECURITY DEFINER functions in migration 009 — kept in sync with them
so the policy intent (deleted users → 404, only owner/admin sees
sensitive data) is the same everywhere.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

UserRole = Literal["owner", "admin", "member", "viewer"]
ADMIN_ROLES: tuple[UserRole, ...] = ("owner", "admin")


async def resolve_tenant_and_role(
    conn: asyncpg.Connection, *, user_id: UUID
) -> tuple[UUID, UserRole]:
    """Look up the caller's tenant + role; raises 404 for inactive users.

    Soft-deleted users (``deleted_at IS NOT NULL``) are treated as
    nonexistent — same logic as the ``auth.tenant_id()`` helper.
    """

    row = await conn.fetchrow(
        """
        select tenant_id, role from public.users
         where id = $1 and deleted_at is null
        """,
        user_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user has no active tenant",
        )
    return UUID(str(row["tenant_id"])), row["role"]


def require_admin(role: UserRole, *, action: str = "this action") -> None:
    """Raise 403 unless ``role`` is ``owner`` or ``admin``.

    Routes call this right after :func:`resolve_tenant_and_role` so the
    error message names the action — gives the FE something specific to
    show ("billing report requires owner/admin role").
    """

    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{action} requires owner/admin role",
        )


async def count_active_owners(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> int:
    """Count owners in a tenant who haven't been soft-deleted.

    Used by every flow that risks orphaning a tenant — self-deletion,
    role downgrade, member removal. Returning 0 means nobody is left to
    administer the tenant; 1 means whoever you're about to touch is the
    last owner.
    """

    return int(
        await conn.fetchval(
            """
            select count(*) from public.users
             where tenant_id = $1
               and role = 'owner'
               and deleted_at is null
            """,
            tenant_id,
        )
        or 0
    )


__all__ = [
    "ADMIN_ROLES",
    "UserRole",
    "count_active_owners",
    "require_admin",
    "resolve_tenant_and_role",
]
