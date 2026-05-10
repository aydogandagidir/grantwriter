"""Library for rotating ``LLM_MASTER_ENCRYPTION_KEY`` over BYOK columns.

The script in ``apps/api/scripts/rotate_master_key.py`` is a thin CLI on
top of this module. The actual decrypt-then-encrypt happens entirely
inside Postgres via a single ``UPDATE`` statement — plaintext keys
never cross the Python boundary, so even an attacker tailing the
script's process memory wouldn't see customer secrets.

Per-tenant flow (one transaction):

1. Compute new ciphertext server-side:
   ``pgp_sym_encrypt(pgp_sym_decrypt(col, OLD), NEW)``
   …skipping NULL columns and tenants with nothing stored.
2. Write one audit row (``tenant.master_key_rotated``) so a human can
   see who got rotated when. No key material in the diff.

If the decrypt step fails (wrong OLD key, corrupt ciphertext, etc.)
the transaction rolls back and the failure is reported on stderr; the
script keeps going so a single bad tenant doesn't block the rest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from src.core.audit import write_audit_event

logger = logging.getLogger(__name__)


# Re-encrypt anthropic + openai columns in one shot. CASE WHEN guards
# against pgp_sym_decrypt(NULL, ...) — that errors otherwise. The WHERE
# at the end skips tenants with neither key set (no work to do).
_ROTATE_SQL = """
update tenant_llm_config
   set anthropic_api_key_encrypted = case
       when anthropic_api_key_encrypted is null then null
       else pgp_sym_encrypt(
              pgp_sym_decrypt(anthropic_api_key_encrypted, $1),
              $2
            )
       end,
       openai_api_key_encrypted = case
       when openai_api_key_encrypted is null then null
       else pgp_sym_encrypt(
              pgp_sym_decrypt(openai_api_key_encrypted, $1),
              $2
            )
       end,
       updated_at = now()
 where tenant_id = $3
   and (anthropic_api_key_encrypted is not null
        or openai_api_key_encrypted is not null)
returning tenant_id
"""


_LIST_TENANTS_WITH_KEYS_SQL = """
select tenant_id
  from tenant_llm_config
 where anthropic_api_key_encrypted is not null
    or openai_api_key_encrypted is not null
 order by updated_at asc
"""


@dataclass
class RotationReport:
    """Aggregate stats for one rotation run.

    ``tenants_skipped`` counts tenants that had no key material to
    rotate; ``errors`` lists ``(tenant_id, error_message)`` for any
    transaction that rolled back.
    """

    tenants_processed: int = 0
    tenants_rotated: int = 0
    tenants_skipped: int = 0
    errors: list[tuple[UUID, str]] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable shape for the CLI's stdout summary."""

        return {
            "tenants_processed": self.tenants_processed,
            "tenants_rotated": self.tenants_rotated,
            "tenants_skipped": self.tenants_skipped,
            "error_count": len(self.errors),
            "errors": [
                {"tenant_id": str(tid), "message": msg}
                for tid, msg in self.errors
            ],
            "dry_run": self.dry_run,
        }


async def rotate_for_tenant(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    old_master: str,
    new_master: str,
) -> bool:
    """Rotate one tenant inside a single transaction.

    Returns ``True`` if the row was updated, ``False`` if no key
    material existed (skip). Decrypt failures bubble as
    :class:`asyncpg.PostgresError` — the caller decides whether to
    keep going.
    """

    async with conn.transaction():
        rotated = await conn.fetchval(
            _ROTATE_SQL, old_master, new_master, tenant_id
        )
        if rotated is None:
            return False
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=None,
            action="tenant.master_key_rotated",
            resource_type="tenant_llm_config",
            resource_id=tenant_id,
            diff={"event": "rotated", "source": "ops_script"},
        )
        return True


async def rotate_all(
    pool: asyncpg.Pool,
    *,
    old_master: str,
    new_master: str,
    only_tenant_id: UUID | None = None,
    dry_run: bool = False,
) -> RotationReport:
    """Walk every tenant_llm_config row and rotate it (or just one).

    The ``old`` / ``new`` keys must differ — passing the same value
    raises :class:`ValueError` immediately, before any DB call. That
    guard catches the most common operator mistake (paste the same key
    into both Railway env vars during the deploy).
    """

    if old_master == new_master:
        raise ValueError("old_master and new_master must differ")
    if not old_master or not new_master:
        raise ValueError("master keys must be non-empty")

    report = RotationReport(dry_run=dry_run)

    async with pool.acquire() as conn:
        if only_tenant_id is not None:
            tenant_ids: list[UUID] = [only_tenant_id]
        else:
            rows = await conn.fetch(_LIST_TENANTS_WITH_KEYS_SQL)
            tenant_ids = [UUID(str(row["tenant_id"])) for row in rows]

    if dry_run:
        report.tenants_processed = len(tenant_ids)
        report.tenants_rotated = len(tenant_ids)  # Would-rotate count.
        logger.info(
            "rotate_dry_run",
            extra={"would_rotate": len(tenant_ids)},
        )
        return report

    # One acquire per tenant — keeps the long-running transaction off
    # the connection between tenants. Important on small pools.
    for tenant_id in tenant_ids:
        report.tenants_processed += 1
        async with pool.acquire() as conn:
            try:
                rotated = await rotate_for_tenant(
                    conn,
                    tenant_id=tenant_id,
                    old_master=old_master,
                    new_master=new_master,
                )
            except asyncpg.PostgresError as exc:
                # Log + continue — operator reruns for the failing tenant
                # after fixing whatever's wrong (corrupt row, key typo).
                logger.error(
                    "rotate_failed_for_tenant",
                    extra={"tenant_id": str(tenant_id), "error": str(exc)},
                )
                report.errors.append((tenant_id, str(exc)))
                continue
        if rotated:
            report.tenants_rotated += 1
        else:
            report.tenants_skipped += 1

    return report


__all__ = ["RotationReport", "rotate_all", "rotate_for_tenant"]
