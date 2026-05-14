"""Iyzico webhook receiver — signature verification + event dispatch.

The receiver is intentionally narrow: it ONLY trusts requests carrying
a valid HMAC signature, persists every event to ``billing_events``
(idempotently — Iyzico retries on transient failures), and dispatches
the small handful of subscription events that actually mutate tenant
state. Everything else is logged at INFO and stored as a record so
operators can reconcile by hand.

**No outbound calls in this module.** Subscription create / list /
cancel land in a follow-up PR (the iyzipay SDK adds ~12 transitive
deps; we keep the receiver pure-stdlib so a webhook regression doesn't
require an SDK upgrade).

Signature scheme (HMAC-SHA256, base64-encoded — the modern Iyzico
webhook convention; older docs reference SHA1, treat that as legacy):

    expected = base64(hmac_sha256(secret, raw_body))
    sent     = request.headers["X-Iyzico-Signature"]
    valid    = hmac.compare_digest(expected, sent)

Comparison is constant-time to defeat timing attacks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from src.billing.plan_mapping import PlanSpec, lookup_plan
from src.core.audit import write_audit_event

logger = logging.getLogger(__name__)


SIGNATURE_HEADER = "X-Iyzico-Signature"
PROVIDER_NAME = "iyzico"


class InvalidSignatureError(Exception):
    """The request body did not match the HMAC signature header."""


# ── Signature ──────────────────────────────────────────────────────────


def compute_signature(*, body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the raw body, base64-encoded.

    Pure helper — exposed so tests can sign synthetic payloads without
    duplicating the algorithm.
    """

    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(*, body: bytes, secret: str, header_value: str | None) -> None:
    """Constant-time signature check; raises :class:`InvalidSignatureError`.

    A missing header is treated identically to a bad signature — never
    surface the difference to the caller (don't help attackers
    differentiate "no auth" from "wrong auth").
    """

    if not header_value:
        raise InvalidSignatureError("missing signature header")
    expected = compute_signature(body=body, secret=secret)
    if not hmac.compare_digest(expected, header_value):
        raise InvalidSignatureError("signature mismatch")


# ── Event parsing ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class IyzicoEvent:
    """The minimal slice of an Iyzico webhook payload we actually use.

    Iyzico's payload schema varies by event type; this struct is the
    conservative intersection. The full body is persisted to
    ``billing_events.payload`` so any extra field stays available.
    """

    event_id: str
    event_type: str
    customer_reference_code: str | None
    plan_reference_code: str | None
    raw: dict[str, Any]


def parse_event(payload: dict[str, Any]) -> IyzicoEvent:
    """Pull the fields we care about from a parsed JSON body."""

    # Iyzico subscription webhooks use ``iyziEventType`` in some
    # examples and ``eventType`` in others — accept both, prefer the
    # current spelling.
    event_type = (
        payload.get("eventType")
        or payload.get("iyziEventType")
        or "unknown"
    )
    event_id = (
        payload.get("eventId")
        or payload.get("iyziEventId")
        or payload.get("token")
        or ""
    )
    if not event_id:
        raise ValueError("event payload has no event_id / token")

    return IyzicoEvent(
        event_id=str(event_id),
        event_type=str(event_type),
        customer_reference_code=payload.get("customerReferenceCode"),
        plan_reference_code=payload.get("pricingPlanReferenceCode")
        or payload.get("subscriptionReferenceCode"),
        raw=payload,
    )


# ── Persistence ────────────────────────────────────────────────────────


async def record_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID | None,
    event: IyzicoEvent,
    amount_eur: float | None = None,
) -> bool:
    """Idempotent INSERT into ``billing_events``.

    Returns ``True`` if a new row was inserted, ``False`` if a row with
    the same ``provider_event_id`` already existed (an Iyzico retry).
    The retry case is the normal flow — we still want the dispatcher to
    re-run any tenant mutations though, so the boolean is informational.
    """

    if tenant_id is None:
        # Schema demands tenant_id NOT NULL. If we got the webhook before
        # the customerReferenceCode resolves to a tenant, drop on the
        # floor with a warning — the operator reconciles by replaying.
        logger.warning(
            "iyzico_event_missing_tenant",
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )
        return False

    new_id = await conn.fetchval(
        """
        insert into billing_events (
          tenant_id, event_type, provider, provider_event_id,
          amount_eur, payload
        ) values (
          $1, $2, $3, $4, $5, $6::jsonb
        )
        on conflict (provider_event_id) do nothing
        returning id
        """,
        tenant_id,
        event.event_type,
        PROVIDER_NAME,
        event.event_id,
        amount_eur,
        json.dumps(event.raw),
    )
    return new_id is not None


# ── Plan-update handler ────────────────────────────────────────────────


_PLAN_ACTIVATING_EVENTS = {
    "subscription.activated",
    "subscription.created",
    "SUBSCRIPTION_ORDER_SUCCESS",  # Iyzico's snake/upper variant — accept both
}
_PLAN_DEACTIVATING_EVENTS = {
    "subscription.cancelled",
    "subscription.canceled",
    "SUBSCRIPTION_CANCELLED",
}


async def apply_plan_update(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    event: IyzicoEvent,
) -> PlanSpec | None:
    """Map the event's plan reference code to a tenant.plan + limit update.

    Returns the resolved :class:`PlanSpec` when a real update happened,
    ``None`` when the event was either irrelevant (not an
    activate/cancel) or carried an unknown reference code.

    On cancel events we downgrade the tenant back to ``starter`` —
    explicit downgrade is safer than leaving them at the prior tier —
    and clear ``tenants.iyzico_subscription_reference`` so the cancel
    endpoint can no longer find a stale ref to call.
    """

    subscription_ref = _extract_subscription_reference(event)

    if event.event_type in _PLAN_DEACTIVATING_EVENTS:
        downgrade = lookup_plan("iyz_starter_monthly") or PlanSpec(
            name="starter", monthly_proposal_limit=3
        )
        await _write_plan(conn, tenant_id=tenant_id, plan=downgrade)
        await _clear_subscription_reference(conn, tenant_id=tenant_id)
        await _audit_plan_change(
            conn, tenant_id=tenant_id, event=event, plan=downgrade
        )
        return downgrade

    if event.event_type not in _PLAN_ACTIVATING_EVENTS:
        return None

    if event.plan_reference_code is None:
        logger.warning(
            "iyzico_event_missing_plan_reference",
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )
        return None

    plan = lookup_plan(event.plan_reference_code)
    if plan is None:
        logger.warning(
            "iyzico_event_unknown_plan_reference",
            extra={
                "event_id": event.event_id,
                "plan_reference_code": event.plan_reference_code,
            },
        )
        return None

    await _write_plan(conn, tenant_id=tenant_id, plan=plan)
    if subscription_ref:
        await _set_subscription_reference(
            conn, tenant_id=tenant_id, reference=subscription_ref
        )
    await _audit_plan_change(conn, tenant_id=tenant_id, event=event, plan=plan)
    return plan


def _extract_subscription_reference(event: IyzicoEvent) -> str | None:
    """Pull the subscriptionReferenceCode from the raw payload if present.

    Iyzico spells it ``subscriptionReferenceCode`` in modern docs;
    older examples use ``referenceCode``. We accept both and prefer
    the modern spelling.
    """

    raw = event.raw
    candidate = raw.get("subscriptionReferenceCode") or raw.get("referenceCode")
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


async def _set_subscription_reference(
    conn: asyncpg.Connection, *, tenant_id: UUID, reference: str
) -> None:
    await conn.execute(
        """
        update tenants
           set iyzico_subscription_reference = $1,
               updated_at = now()
         where id = $2
        """,
        reference,
        tenant_id,
    )


async def _clear_subscription_reference(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> None:
    await conn.execute(
        """
        update tenants
           set iyzico_subscription_reference = null,
               updated_at = now()
         where id = $1
        """,
        tenant_id,
    )


async def _write_plan(
    conn: asyncpg.Connection, *, tenant_id: UUID, plan: PlanSpec
) -> None:
    await conn.execute(
        """
        update tenants
           set plan = $1,
               monthly_proposal_limit = $2,
               updated_at = now()
         where id = $3
        """,
        plan.name,
        plan.monthly_proposal_limit,
        tenant_id,
    )


async def _audit_plan_change(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    event: IyzicoEvent,
    plan: PlanSpec,
) -> None:
    """One audit row per real plan change — short diff, no PII."""

    await write_audit_event(
        conn,
        tenant_id=tenant_id,
        user_id=None,  # Iyzico is the actor, not a logged-in user
        action="tenant.plan_changed",
        resource_type="tenant",
        resource_id=tenant_id,
        diff={
            "plan": plan.name,
            "monthly_limit": str(plan.monthly_proposal_limit),
            "source": "iyzico",
            "event_type": event.event_type,
        },
    )


__all__ = [
    "PROVIDER_NAME",
    "SIGNATURE_HEADER",
    "InvalidSignatureError",
    "IyzicoEvent",
    "apply_plan_update",
    "compute_signature",
    "parse_event",
    "record_event",
    "verify_signature",
]
