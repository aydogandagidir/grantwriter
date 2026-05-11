"""Tenant usage aggregations over ``tenant_usage_log``.

Read counterpart to :mod:`src.llm.cost_tracker`. Two queries:

- :func:`aggregate_window` — totals between two timestamps. Used for
  "this month so far" and "previous month" snapshots.
- :func:`monthly_series` — bucketed series by ``date_trunc('month', …)``
  for the chart on the usage page. Returns only months that have rows
  (the UI fills gaps); cheaper than ``generate_series`` and matches the
  client-side rendering pattern.

Index ``idx_usage_tenant_time`` on ``(tenant_id, created_at desc)``
covers both queries — see migration 008.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageWindow:
    """Aggregate over a single time window."""

    llm_call_count: int
    byok_call_count: int
    managed_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_cost_usd: Decimal


@dataclass(frozen=True)
class MonthBucket:
    """One bucket of the monthly series."""

    month: date
    cost_usd: Decimal
    call_count: int
    byok_calls: int


_ZERO_USD = Decimal("0.000000")
"""6-decimal-place zero matching the SQL ``::numeric(14, 6)`` cast.

Pydantic serialises Decimals preserving their exponent — keeping all
zero-cost paths at the same scale gives the API a stable, predictable
shape (``"0.000000"`` everywhere, never ``"0"``)."""

_ZERO_WINDOW = UsageWindow(
    llm_call_count=0,
    byok_call_count=0,
    managed_call_count=0,
    total_input_tokens=0,
    total_output_tokens=0,
    total_cached_tokens=0,
    total_cost_usd=_ZERO_USD,
)


async def aggregate_window(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    start: datetime,
    end: datetime,
) -> UsageWindow:
    """Sum tokens + cost for ``[start, end)``.

    ``start``/``end`` are timezone-aware UTC datetimes; comparison runs
    against ``timestamptz`` so DST and offset are not a concern. Empty
    windows return :data:`_ZERO_WINDOW` rather than raising — the
    caller treats "no usage yet" as a valid state.
    """

    row = await conn.fetchrow(
        """
        select
          count(*) filter (where event_type = 'llm_call')          as llm_call_count,
          count(*) filter (where used_byok)                        as byok_call_count,
          count(*) filter (where used_byok is not true)            as managed_call_count,
          coalesce(sum(input_tokens), 0)::bigint                   as total_input_tokens,
          coalesce(sum(output_tokens), 0)::bigint                  as total_output_tokens,
          coalesce(sum(cached_tokens), 0)::bigint                  as total_cached_tokens,
          coalesce(sum(cost_usd), 0)::numeric(14, 6)               as total_cost_usd
        from tenant_usage_log
        where tenant_id = $1
          and created_at >= $2
          and created_at <  $3
        """,
        tenant_id,
        start,
        end,
    )
    if row is None:
        return _ZERO_WINDOW
    cost = row["total_cost_usd"]
    return UsageWindow(
        llm_call_count=int(row["llm_call_count"] or 0),
        byok_call_count=int(row["byok_call_count"] or 0),
        managed_call_count=int(row["managed_call_count"] or 0),
        total_input_tokens=int(row["total_input_tokens"] or 0),
        total_output_tokens=int(row["total_output_tokens"] or 0),
        total_cached_tokens=int(row["total_cached_tokens"] or 0),
        # Explicit None check — `Decimal("0.000000")` is falsy, so a
        # truthy `or` would silently downgrade the precision.
        total_cost_usd=cost if cost is not None else _ZERO_USD,
    )


async def monthly_series(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    months: int = 12,
) -> list[MonthBucket]:
    """Return up to ``months`` buckets, oldest first.

    Months without activity are omitted from the result; the FE chart
    fills gaps. ``date_trunc('month', …)`` returns a ``timestamptz`` —
    we cast to ``date`` so the JSON is just ``"2026-05-01"`` rather
    than a midnight ISO timestamp.
    """

    if months < 1:
        raise ValueError("months must be >= 1")

    rows = await conn.fetch(
        """
        select
          (date_trunc('month', created_at))::date       as month,
          coalesce(sum(cost_usd), 0)::numeric(14, 6)    as cost_usd,
          count(*)                                      as call_count,
          count(*) filter (where used_byok)             as byok_calls
        from tenant_usage_log
        where tenant_id = $1
          and created_at >= date_trunc('month', now()) - ($2::int - 1) * interval '1 month'
        group by month
        order by month asc
        """,
        tenant_id,
        months,
    )
    return [
        MonthBucket(
            month=row["month"],
            cost_usd=row["cost_usd"] if row["cost_usd"] is not None else _ZERO_USD,
            call_count=int(row["call_count"] or 0),
            byok_calls=int(row["byok_calls"] or 0),
        )
        for row in rows
    ]


__all__ = [
    "MonthBucket",
    "UsageWindow",
    "aggregate_window",
    "monthly_series",
]
