"""Iyzico subscription reference codes → internal plan / quota.

When Iyzico sends a ``subscription.activated`` event, the payload carries
an opaque reference code (configured in the merchant panel when the
subscription product is created). This table maps those codes to our
internal plan name and the matching ``monthly_proposal_limit`` value.

Keep this in sync with whatever you configure in the Iyzico merchant
panel — if a webhook arrives carrying an unknown code, the receiver
logs a warning and skips the plan update (the event is still recorded
in ``billing_events`` so the operator can reconcile by hand).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlanName = Literal["starter", "pro", "agency", "enterprise"]


@dataclass(frozen=True)
class PlanSpec:
    """Internal description of a single plan tier."""

    name: PlanName
    monthly_proposal_limit: int


# Effectively-unlimited cap for the Agency tier — keeps the column NOT
# NULL constraint happy without creating a special "is_unlimited" path.
_UNLIMITED = 999_999

# Reference codes are placeholders. Replace with the real codes from
# the Iyzico merchant panel once the subscription products are created
# (Faz 1 sprint planning task).
PLAN_BY_REFERENCE_CODE: dict[str, PlanSpec] = {
    "iyz_starter_monthly": PlanSpec(name="starter", monthly_proposal_limit=3),
    "iyz_pro_monthly": PlanSpec(name="pro", monthly_proposal_limit=15),
    "iyz_agency_monthly": PlanSpec(name="agency", monthly_proposal_limit=_UNLIMITED),
    "iyz_enterprise_monthly": PlanSpec(
        name="enterprise", monthly_proposal_limit=_UNLIMITED
    ),
}


def lookup_plan(reference_code: str) -> PlanSpec | None:
    """Return the matching :class:`PlanSpec` or ``None`` if unknown.

    Callers should treat ``None`` as "log + skip the plan update". The
    underlying webhook event is still persisted in ``billing_events`` so
    nothing is lost — an operator can reconcile by hand.
    """

    return PLAN_BY_REFERENCE_CODE.get(reference_code)


__all__ = ["PLAN_BY_REFERENCE_CODE", "PlanName", "PlanSpec", "lookup_plan"]
