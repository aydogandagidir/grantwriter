"""Append-only audit log writes.

`audit_log` records every privileged write — proposal updates, citation
verifications, compliance reviews, and BYOK key changes (action only,
never the key value). The schema is defined in
``infra/supabase/migrations/20260508120800_usage_billing_audit.sql``.

The actions enumerated in docs/09 §9 use a dotted namespace
(``auth.login``, ``tenant.llm_config_updated``, ``proposal.exported`` …).
Callers pass the action string verbatim; this module is intentionally
schema-light so adding new actions doesn't require code changes here.

**Secret-leak guard.** :func:`write_audit_event` rejects any string value
in ``diff`` longer than the safe threshold below. BYOK keys are 50+ chars
(``sk-ant-…`` / ``sk-proj-…``); allowed values like ``"set"`` /
``"cleared"`` / role names / UUIDs all pass. This is belt-and-suspenders
against a future caller accidentally putting a plaintext key in the diff.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

# Anthropic / OpenAI keys start above 40 chars; UUIDs are 36 chars; role
# names, "set"/"cleared" sentinels, and ISO timestamps all fit comfortably.
_MAX_DIFF_VALUE_LEN = 36


def _validate_diff(diff: dict[str, Any]) -> None:
    """Raise if any leaf string value in ``diff`` exceeds the safe threshold.

    Recurses into nested dicts / lists. Non-string scalars (bool, int, None,
    UUID-as-uuid) are always fine. The helper is internal — callers should
    just keep diffs short.
    """

    def _walk(value: Any) -> None:
        if isinstance(value, str) and len(value) > _MAX_DIFF_VALUE_LEN:
            raise ValueError(
                f"audit diff value too long ({len(value)} chars > "
                f"{_MAX_DIFF_VALUE_LEN}); likely a leaked secret. "
                "Audit diffs must contain only short sentinels (set/cleared/…)."
            )
        if isinstance(value, dict):
            for nested in value.values():
                _walk(nested)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(diff)


async def write_audit_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID | None,
    user_id: UUID | None,
    action: str,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    diff: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> UUID:
    """Insert one row into ``audit_log`` and return its id.

    Raises :class:`ValueError` if ``diff`` carries a suspicious-looking
    long string (see :func:`_validate_diff`). Database errors propagate.

    The structured logger emits the metadata fields (action, resource,
    tenant_id) but NEVER the diff body — diffs may contain non-secret but
    still-sensitive context (role changes, e.g.) and the audit table is
    the source of truth.
    """

    if diff is not None:
        _validate_diff(diff)

    new_id = await conn.fetchval(
        """
        insert into audit_log (
          tenant_id, user_id, action,
          resource_type, resource_id,
          diff, ip_address, user_agent
        ) values (
          $1, $2, $3,
          $4, $5,
          $6::jsonb, $7::inet, $8
        ) returning id
        """,
        tenant_id,
        user_id,
        action,
        resource_type,
        resource_id,
        json.dumps(diff) if diff is not None else None,
        ip_address,
        user_agent,
    )
    logger.info(
        "audit_event_written",
        extra={
            "action": action,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
        },
    )
    return UUID(str(new_id))


__all__ = ["write_audit_event"]
