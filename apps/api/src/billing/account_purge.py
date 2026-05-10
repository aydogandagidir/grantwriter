"""Hard-delete users whose grace window has expired.

Pairs with the soft-delete written by ``DELETE /me/account`` and
``DELETE /tenant/members/{id}``: those flows set
``public.users.deleted_at = now()``. After the configured grace window
(30 days per docs/09 §3.2) this purger anonymises the trailing
references in audit / usage / proposals and then deletes the row.

Per-user transaction shape (one BEGIN/COMMIT per user — a single bad
row never blocks the rest of the run):

    UPDATE proposals       SET created_by = NULL WHERE created_by = $1
    UPDATE audit_log       SET user_id    = NULL WHERE user_id    = $1
    UPDATE tenant_usage_log SET user_id   = NULL WHERE user_id    = $1
    DELETE FROM public.users WHERE id = $1
    INSERT INTO audit_log (..., 'user.account_purged', ...)  -- system actor

If ``proposals.created_by`` is still NOT NULL on the local DB
(migration 005 mismatch with docs/09 §3.2), the proposals UPDATE fails
the transaction → user is reported in :attr:`PurgeReport.errors` and
the script keeps going. Operator schema-fixes and reruns.

The purge audit row is written with ``user_id = NULL`` because the
acting user has just been deleted — the row's ``resource_id`` carries
the purged user's id for cross-referencing, and the diff names the
days_since_deletion so an operator can spot anomalies (e.g. a script
run with the wrong --grace-days).

Note: ``auth.users`` is owned by Supabase Auth and is NOT touched here.
Supabase exposes a separate admin API for that; deleting auth.users
should happen out of band (Faz 2 — wire the Supabase admin client).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


_LIST_CANDIDATES_SQL = """
select id, tenant_id, deleted_at
  from public.users
 where deleted_at is not null
   and deleted_at < now() - ($1::int * interval '1 day')
 order by deleted_at asc
 limit $2
"""

# Hard cap on per-run blast radius. 10k is generous for a nightly job
# that typically processes single-digit users; raising it requires a
# code change AND an explicit operator decision.
_MAX_LIMIT = 10_000


@dataclass
class PurgeReport:
    """Aggregate stats — JSON-serialised by the CLI for stdout output."""

    candidates: int = 0
    purged: int = 0
    skipped: int = 0  # for dry-run, this == candidates
    errors: list[tuple[UUID, str]] = field(default_factory=list)
    grace_days: int = 30
    dry_run: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "candidates": self.candidates,
            "purged": self.purged,
            "skipped": self.skipped,
            "error_count": len(self.errors),
            "errors": [
                {"user_id": str(uid), "message": msg}
                for uid, msg in self.errors
            ],
            "grace_days": self.grace_days,
            "dry_run": self.dry_run,
        }


async def _purge_one(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    deleted_at: datetime,
    now: datetime,
) -> None:
    """Anonymise references + delete the user row in one transaction.

    Raises whatever asyncpg raises if the transaction fails — the caller
    catches and records the per-user error.
    """

    days_since = max((now - deleted_at).days, 0)

    # docs/09 §3.2 says ``proposals.created_by → NULL`` on hard-delete.
    # The schema in migration 005 still has it NOT NULL (planned change
    # in a follow-up); on partially-migrated DBs the column may not
    # exist yet. Detect once, outside the transaction — once a statement
    # fails inside a Postgres transaction the transaction is poisoned
    # and a try/except in-loop wouldn't recover.
    proposals_column_present = bool(
        await conn.fetchval(
            """
            select exists (
              select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name = 'proposals'
                 and column_name = 'created_by'
            )
            """
        )
    )

    async with conn.transaction():
        if proposals_column_present:
            await conn.execute(
                "update proposals set created_by = null where created_by = $1",
                user_id,
            )
        else:
            logger.warning(
                "purge_proposals_column_missing",
                extra={"user_id": str(user_id)},
            )
        await conn.execute(
            "update audit_log set user_id = null where user_id = $1",
            user_id,
        )
        await conn.execute(
            "update tenant_usage_log set user_id = null where user_id = $1",
            user_id,
        )
        await conn.execute(
            "delete from public.users where id = $1", user_id
        )
        # System-action audit row — user_id stays NULL (actor was just
        # deleted). The diff carries days_since_deletion so an operator
        # can sanity-check the grace-window enforcement.
        await conn.execute(
            """
            insert into audit_log (
              tenant_id, user_id, action,
              resource_type, resource_id, diff
            ) values (
              $1, null, 'user.account_purged',
              'user', $2, $3::jsonb
            )
            """,
            tenant_id,
            user_id,
            json.dumps({"days_since_deletion": str(days_since)}),
        )


async def purge_expired_accounts(
    pool: asyncpg.Pool,
    *,
    grace_days: int = 30,
    limit: int = 1000,
    dry_run: bool = False,
) -> PurgeReport:
    """List candidates older than ``grace_days``, then purge each.

    Defaults match docs/09 §3.2 (30-day grace window). The hard cap of
    ``limit=1000`` per run limits blast radius if an operator dials in
    a too-aggressive grace by accident.
    """

    if grace_days < 0:
        raise ValueError("grace_days must be non-negative")
    if not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit must be in [1, {_MAX_LIMIT}]")

    report = PurgeReport(grace_days=grace_days, dry_run=dry_run)

    async with pool.acquire() as conn:
        candidates = await conn.fetch(
            _LIST_CANDIDATES_SQL, grace_days, limit
        )

    report.candidates = len(candidates)

    if dry_run:
        report.skipped = len(candidates)
        logger.info(
            "purge_dry_run",
            extra={"candidates": len(candidates), "grace_days": grace_days},
        )
        return report

    for row in candidates:
        user_id = UUID(str(row["id"]))
        tenant_id = UUID(str(row["tenant_id"]))
        deleted_at = row["deleted_at"]
        # Compare in UTC space — deleted_at is timestamptz, so its
        # subtraction yields a naive timedelta in seconds.
        if deleted_at.tzinfo is not None:
            now_aware = datetime.now(deleted_at.tzinfo)
        else:
            now_aware = datetime.utcnow()

        async with pool.acquire() as conn:
            try:
                await _purge_one(
                    conn,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    deleted_at=deleted_at,
                    now=now_aware,
                )
            except asyncpg.PostgresError as exc:
                logger.error(
                    "purge_failed_for_user",
                    extra={
                        "user_id": str(user_id),
                        "error": str(exc),
                    },
                )
                report.errors.append((user_id, str(exc)))
                continue

        report.purged += 1
        logger.info(
            "purge_succeeded",
            extra={
                "user_id": str(user_id),
                "tenant_id": str(tenant_id),
                "grace_days": grace_days,
            },
        )

    return report


__all__ = ["PurgeReport", "purge_expired_accounts"]
