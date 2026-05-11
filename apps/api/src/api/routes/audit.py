"""Read-side endpoint for the audit log.

Pairs with :mod:`src.core.audit` (the writer). Mirrors the spirit of
the ``audit_admin_select`` RLS policy in migration 010 — only owner or
admin roles see their tenant's events. Members making the changes can
audit themselves, but they don't get to read the aggregated trail.

Filtering surface (all optional, all combinable):

- ``action``        — exact match (e.g. ``tenant.llm_config_updated``)
- ``resource_type`` — exact match (e.g. ``tenant_llm_config``)
- ``before``        — ISO-8601 datetime; returns rows strictly older
                      than the cursor (keyset pagination, newest-first)
- ``limit``         — 1..200, default 50

Pagination is keyset on ``created_at`` because the table has a
``(tenant_id, created_at desc)`` index and audit rows monotonically
grow. Offset pagination would scan past every prior page on each
fetch — wrong for an audit table that compounds over time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import require_admin, resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tenant/audit-log", tags=["audit-log"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# ── Response models ────────────────────────────────────────────────────


class AuditLogEntry(BaseModel):
    """One row of the audit log, post-decode.

    ``ip_address`` is a plain string — asyncpg returns ``inet`` as
    :class:`ipaddress.IPv4Address` / ``IPv6Address``, which we stringify
    for JSON. ``diff`` is the JSON-decoded jsonb body (the writer always
    serialises a dict; here we accept whatever shape the writer wrote)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    action: str
    resource_type: str | None
    resource_id: UUID | None
    diff: dict[str, Any] | None
    user_id: UUID | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    """One page of the audit log + the next-page cursor.

    ``next_before`` is ``None`` when the page is the last one in the
    range. Otherwise the FE sends it back as the ``before`` query param
    to fetch the next older page."""

    model_config = ConfigDict(frozen=True)

    entries: list[AuditLogEntry]
    next_before: datetime | None


# ── Helpers ────────────────────────────────────────────────────────────


def _decode_diff(raw: Any) -> dict[str, Any] | None:
    """Best-effort jsonb → dict.

    asyncpg returns jsonb as a JSON-encoded string by default. When the
    writer stored ``NULL`` we get ``None``. The audit writer always
    encodes a dict so any non-dict result here is a corruption signal —
    skip rather than crash so a single bad row doesn't break the page.
    """

    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str | bytes):
        try:
            decoded = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _row_to_entry(row: asyncpg.Record) -> AuditLogEntry:
    return AuditLogEntry(
        id=UUID(str(row["id"])),
        action=str(row["action"]),
        resource_type=row["resource_type"],
        resource_id=UUID(str(row["resource_id"])) if row["resource_id"] else None,
        diff=_decode_diff(row["diff"]),
        user_id=UUID(str(row["user_id"])) if row["user_id"] else None,
        # ``inet`` → IPv4Address / IPv6Address; stringify for the wire.
        ip_address=str(row["ip_address"]) if row["ip_address"] else None,
        user_agent=row["user_agent"],
        created_at=row["created_at"],
    )


# ── Route ──────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=AuditLogPage,
    summary="Read the tenant's audit log (owner/admin only)",
)
async def list_audit_log(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    action: Annotated[
        str | None,
        Query(description="Exact match — e.g. tenant.llm_config_updated"),
    ] = None,
    resource_type: Annotated[
        str | None,
        Query(description="Exact match — e.g. tenant_llm_config"),
    ] = None,
    before: Annotated[
        datetime | None,
        Query(description="Cursor — return rows strictly older than this"),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=_MAX_LIMIT, description="Page size, max 200")
    ] = _DEFAULT_LIMIT,
) -> AuditLogPage:
    """Page through ``audit_log`` for the caller's tenant, newest first.

    - 200: the page (possibly empty).
    - 403: caller is not owner/admin.
    - 404: caller has no active tenant.

    The query is parameterised by tenant + optional filters and uses
    ``order by created_at desc limit limit + 1`` so we can detect the
    last page without a separate count query.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="audit log read")

    # +1 sentinel: if we got more than ``limit`` rows, the next page
    # exists and we set the cursor; otherwise we're done.
    rows = await conn.fetch(
        """
        select id, action, resource_type, resource_id,
               diff, user_id, ip_address, user_agent, created_at
          from audit_log
         where tenant_id = $1
           and ($2::text       is null or action        = $2)
           and ($3::text       is null or resource_type = $3)
           and ($4::timestamptz is null or created_at   < $4)
         order by created_at desc
         limit $5
        """,
        tenant_id,
        action,
        resource_type,
        before,
        limit + 1,
    )

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_before = page_rows[-1]["created_at"] if has_more and page_rows else None

    entries = [_row_to_entry(row) for row in page_rows]
    logger.info(
        "audit_log_listed",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "action_filter": action,
            "resource_filter": resource_type,
            "row_count": len(entries),
            "has_more": has_more,
        },
    )
    return AuditLogPage(entries=entries, next_before=next_before)


__all__ = ["AuditLogEntry", "AuditLogPage", "router"]
