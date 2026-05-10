"""Tenant LLM usage + budget endpoint.

``GET /api/v1/tenant/usage`` returns the current month's totals, a 12-month
series for the chart, and the live budget status. The frontend renders
this on the Settings → Usage page next to the BYOK config form.

Auth: bearer JWT, owner/admin only — mirrors the
``usage_admin_select`` RLS policy on ``tenant_usage_log`` (see migration
010). Members make LLM calls but don't get to read aggregate spend.

The endpoint is read-only, idempotent, and cheap: two indexed queries
against ``tenant_usage_log`` plus one row from ``tenant_llm_config``.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from src.billing import quota as quota_module
from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import require_admin, resolve_tenant_and_role
from src.llm import usage_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tenant/usage", tags=["usage"])

_DEFAULT_MONTHS = 12


# ── Response shape ─────────────────────────────────────────────────────


class UsageTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm_call_count: int
    byok_call_count: int
    managed_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_cost_usd: Decimal


class MonthlyUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: date
    cost_usd: Decimal
    call_count: int
    byok_calls: int


class BudgetStatus(BaseModel):
    """Snapshot of cap + threshold + how close we are.

    ``headroom_usd`` is ``budget - current``; negative when over budget,
    ``None`` when there is no cap. The booleans let the UI gate behaviour
    without re-doing the arithmetic.
    """

    model_config = ConfigDict(frozen=True)

    monthly_budget_usd: Decimal | None
    alert_threshold_usd: Decimal | None
    current_month_usd: Decimal
    at_alert_threshold: bool
    over_budget: bool
    headroom_usd: Decimal | None


class PlanQuota(BaseModel):
    """Per-tenant monthly proposal quota — read-only snapshot.

    Mirrors the FE-facing fields of :class:`src.billing.quota.QuotaSnapshot`.
    Counters reset lazily at the start of each calendar month; if the
    period rolled over but no ``/generate`` has run yet,
    ``used_this_month`` reads ``0``.
    """

    model_config = ConfigDict(frozen=True)

    plan: Literal["starter", "pro", "agency", "enterprise"]
    monthly_limit: int
    used_this_month: int
    remaining: int
    period_start: date


class TenantUsageReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_start: datetime
    period_end: datetime
    current_month: UsageTotals
    monthly_series: list[MonthlyUsage]
    budget: BudgetStatus
    plan_quota: PlanQuota


# ── Helpers ────────────────────────────────────────────────────────────


def _current_month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return ``(month_start, now)`` for the aggregation window."""

    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return month_start, now


async def _read_budget_columns(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(monthly_budget_usd, alert_threshold_usd)`` or ``(None, None)``.

    Missing row is normal (tenant never opened the LLM config page) — both
    fields default to NULL, the same as never-set columns.
    """

    row = await conn.fetchrow(
        """
        select monthly_budget_usd, alert_threshold_usd
          from tenant_llm_config
         where tenant_id = $1
        """,
        tenant_id,
    )
    if row is None:
        return None, None
    return row["monthly_budget_usd"], row["alert_threshold_usd"]


def _compute_budget_status(
    *,
    budget: Decimal | None,
    threshold: Decimal | None,
    current: Decimal,
) -> BudgetStatus:
    """Pure computation — no IO. Easy to unit-test if we ever need to."""

    over = budget is not None and current > budget
    at_alert = threshold is not None and current >= threshold
    headroom = (budget - current) if budget is not None else None
    return BudgetStatus(
        monthly_budget_usd=budget,
        alert_threshold_usd=threshold,
        current_month_usd=current,
        at_alert_threshold=at_alert,
        over_budget=over,
        headroom_usd=headroom,
    )


# ── Route ──────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=TenantUsageReport,
    summary="Tenant LLM usage — current month, 12-month series, budget status",
)
async def get_tenant_usage(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> TenantUsageReport:
    """Aggregate ``tenant_usage_log`` for the caller's tenant.

    - 200: report payload.
    - 403: caller is not owner/admin (mirrors RLS ``usage_admin_select``).
    - 404: caller has no active tenant.

    The response is computed from two queries plus one config read; on a
    busy tenant (~10K rows / month) this stays well under the 500 ms p95
    target documented in CLAUDE.md.
    """

    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="usage report")

    now = datetime.now(UTC)
    period_start, period_end = _current_month_bounds(now)

    window = await usage_query.aggregate_window(
        conn, tenant_id=tenant_id, start=period_start, end=period_end
    )
    series = await usage_query.monthly_series(
        conn, tenant_id=tenant_id, months=_DEFAULT_MONTHS
    )
    budget_amount, alert_amount = await _read_budget_columns(
        conn, tenant_id=tenant_id
    )
    quota = await quota_module.peek_quota(conn, tenant_id=tenant_id)

    report = TenantUsageReport(
        period_start=period_start,
        period_end=period_end,
        current_month=UsageTotals(
            llm_call_count=window.llm_call_count,
            byok_call_count=window.byok_call_count,
            managed_call_count=window.managed_call_count,
            total_input_tokens=window.total_input_tokens,
            total_output_tokens=window.total_output_tokens,
            total_cached_tokens=window.total_cached_tokens,
            total_cost_usd=window.total_cost_usd,
        ),
        monthly_series=[
            MonthlyUsage(
                month=bucket.month,
                cost_usd=bucket.cost_usd,
                call_count=bucket.call_count,
                byok_calls=bucket.byok_calls,
            )
            for bucket in series
        ],
        budget=_compute_budget_status(
            budget=budget_amount,
            threshold=alert_amount,
            current=window.total_cost_usd,
        ),
        plan_quota=PlanQuota(
            plan=quota.plan,  # type: ignore[arg-type]
            monthly_limit=quota.monthly_limit,
            used_this_month=quota.used_this_month,
            remaining=quota.remaining,
            period_start=quota.period_start,
        ),
    )

    logger.info(
        "tenant_usage_returned",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "current_month_cost": float(window.total_cost_usd),
            "current_month_calls": window.llm_call_count,
            "over_budget": report.budget.over_budget,
            "at_alert": report.budget.at_alert_threshold,
        },
    )
    return report


__all__ = [
    "BudgetStatus",
    "MonthlyUsage",
    "TenantUsageReport",
    "UsageTotals",
    "router",
]
