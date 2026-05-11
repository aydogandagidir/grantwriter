"""Per-tenant monthly proposal quota.

Enforces the plan-level cap stored on ``tenants.monthly_proposal_limit``
(docs/09 §8 — Starter 3, Pro 15, Agency effectively unlimited via a
large limit value). Two operations:

- :func:`consume_quota` — runs a single atomic ``UPDATE … RETURNING`` that
  rolls the counter into the current calendar month if needed and either
  increments or refuses the request. The single-statement design closes
  the read-then-write race that two parallel ``/generate`` requests
  would otherwise hit.
- :func:`peek_quota` — read-only snapshot for the usage UI.

**Lazy period rollover.** ``billing_period_start`` is nullable and starts
at NULL on a fresh tenant; comparing NULL against a date is itself NULL
in SQL, so we ``coalesce`` to a far-past date before the comparison.
That keeps a fresh tenant's first call from being denied because their
period start hasn't been initialised yet.

**Failure semantics.** A failed saga does NOT refund the counter — the
LLM provider has already charged for the burst, and the counter is
"the user pressed Generate this many times this month", not "the user
got a successful draft". UX-wise the FE shows ``X / Y proposals used``
either way, and the user retries with a fresh attempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaSnapshot:
    """Read-only view of the current period."""

    plan: str
    monthly_limit: int
    used_this_month: int
    period_start: date

    @property
    def remaining(self) -> int:
        return max(self.monthly_limit - self.used_this_month, 0)

    @property
    def is_exceeded(self) -> bool:
        return self.used_this_month >= self.monthly_limit


@dataclass(frozen=True)
class QuotaResult:
    """Outcome of one :func:`consume_quota` call."""

    allowed: bool
    snapshot: QuotaSnapshot


class TenantNotFoundError(Exception):
    """Raised when ``consume_quota`` / ``peek_quota`` get a non-existent tenant id."""


# ── SQL ────────────────────────────────────────────────────────────────


# Single UPDATE: roll-over to current month if stale, then either bump
# the counter or refuse. WHERE rejects when the user is past the limit
# AND not entitled to a fresh period — the empty result tells the caller
# to 402.
#
# coalesce(billing_period_start, '1900-01-01') makes a NULL period (fresh
# tenant) compare as "stale", so the very first call rolls into the
# current month and counts as 1.
_CONSUME_SQL = """
update tenants
   set monthly_proposals_used = case
       when coalesce(billing_period_start, date '1900-01-01')
              < date_trunc('month', now())::date
         then 1
         else monthly_proposals_used + 1
       end,
       billing_period_start = date_trunc('month', now())::date,
       updated_at = now()
 where id = $1
   and (
     coalesce(billing_period_start, date '1900-01-01')
       < date_trunc('month', now())::date
     or monthly_proposals_used < monthly_proposal_limit
   )
returning
   monthly_proposals_used,
   monthly_proposal_limit,
   billing_period_start,
   plan
"""

# Read-only — never mutates. Returns the snapshot the user "should see"
# right now: if the period rolled over but no consume() has run yet,
# used_this_month reads as 0 even though the row still says e.g. 3.
_PEEK_SQL = """
select
  case
    when coalesce(billing_period_start, date '1900-01-01')
           < date_trunc('month', now())::date
      then 0
    else monthly_proposals_used
  end as used_this_month,
  monthly_proposal_limit,
  date_trunc('month', now())::date as current_period_start,
  plan
from tenants
where id = $1
"""


# ── Public API ─────────────────────────────────────────────────────────


async def consume_quota(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> QuotaResult:
    """Atomically claim one quota slot for the current calendar month.

    Returns ``allowed=True`` with the post-increment snapshot when the
    tenant had headroom, or ``allowed=False`` with a peeked snapshot when
    the limit is hit. Raises :class:`TenantNotFoundError` if the tenant
    row doesn't exist (operator-level error — the caller should 404).
    """

    row = await conn.fetchrow(_CONSUME_SQL, tenant_id)
    if row is not None:
        snapshot = QuotaSnapshot(
            plan=str(row["plan"]),
            monthly_limit=int(row["monthly_proposal_limit"]),
            used_this_month=int(row["monthly_proposals_used"]),
            period_start=row["billing_period_start"],
        )
        logger.info(
            "quota_consumed",
            extra={
                "tenant_id": str(tenant_id),
                "plan": snapshot.plan,
                "used": snapshot.used_this_month,
                "limit": snapshot.monthly_limit,
            },
        )
        return QuotaResult(allowed=True, snapshot=snapshot)

    # WHERE didn't match → either tenant doesn't exist OR limit is hit.
    # Disambiguate with a peek; this is a rare path so the extra round-
    # trip is fine.
    snapshot = await peek_quota(conn, tenant_id=tenant_id)
    logger.info(
        "quota_exceeded",
        extra={
            "tenant_id": str(tenant_id),
            "plan": snapshot.plan,
            "used": snapshot.used_this_month,
            "limit": snapshot.monthly_limit,
        },
    )
    return QuotaResult(allowed=False, snapshot=snapshot)


async def peek_quota(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> QuotaSnapshot:
    """Return the live snapshot without mutating anything."""

    row = await conn.fetchrow(_PEEK_SQL, tenant_id)
    if row is None:
        raise TenantNotFoundError(str(tenant_id))
    return QuotaSnapshot(
        plan=str(row["plan"]),
        monthly_limit=int(row["monthly_proposal_limit"]),
        used_this_month=int(row["used_this_month"]),
        period_start=row["current_period_start"],
    )


__all__ = [
    "QuotaResult",
    "QuotaSnapshot",
    "TenantNotFoundError",
    "consume_quota",
    "peek_quota",
]
