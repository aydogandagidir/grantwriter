"""Iyzico billing endpoints — webhook receiver + outbound checkout/cancel.

The webhook receiver (``POST /iyzico-webhook``) is authentication-free
(Iyzico calls it from outside the authenticated zone) but HMAC-locked:
the body must carry a valid ``X-Iyzico-Signature`` header. The two
outbound endpoints (``POST /checkout``, ``DELETE /subscription``) are
JWT-authenticated tenant-scoped — checkout is member+ (any logged-in
member can initiate an upgrade), cancel is admin-only (subscription
state is shared across the tenant).

Flow per webhook request:

1. Read raw body bytes (signature is over the bytes-on-the-wire — a
   re-serialised JSON dict would have a different signature).
2. Verify HMAC. Bad → 401, no DB write.
3. Parse the JSON event.
4. Look up tenant by ``customerReferenceCode`` →
   ``tenants.iyzico_customer_id``.
5. Persist the event (idempotent — Iyzico retries on transient
   failures and would dupe ``provider_event_id`` otherwise).
6. Dispatch plan-mutating events (subscription
   activated/created/cancelled), set/clear
   ``tenants.iyzico_subscription_reference`` accordingly.

Returns ``200 {"received": true}`` on success — Iyzico interprets any
non-2xx as "retry".

Outbound checkout flow:

1. Member POSTs ``{plan_reference_code}``.
2. Backend looks up tenant + caller email.
3. ``IyzicoClient.create_subscription_checkout`` → hosted form URL.
4. FE redirects user to URL; Iyzico hosts card form, redirects back to
   ``IYZICO_CALLBACK_URL``; the eventual webhook updates the plan.

Outbound cancel flow:

1. Admin DELETEs ``/subscription``.
2. Backend reads ``tenants.iyzico_subscription_reference`` (404 if NULL).
3. ``IyzicoClient.cancel_subscription`` calls Iyzico.
4. The webhook will eventually arrive and clear the column + downgrade
   the tenant; the endpoint returns immediately with the cancel result.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.billing import iyzico
from src.billing.iyzico_client import IyzicoClient, IyzicoOutboundError
from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.config import SettingsDep
from src.core.db import get_db
from src.core.tenant import require_admin, resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.post(
    "/iyzico-webhook",
    summary="Iyzico subscription webhook receiver (HMAC-authenticated)",
)
async def iyzico_webhook(
    request: Request,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    signature: Annotated[
        str | None, Header(alias="X-Iyzico-Signature")
    ] = None,
) -> dict[str, Any]:
    """Verify, persist, and dispatch one Iyzico webhook delivery.

    Status codes:
    - 200: accepted (idempotent — duplicate event still 200).
    - 400: body wasn't valid JSON.
    - 401: bad / missing HMAC signature.
    - 503: ``IYZICO_WEBHOOK_SECRET`` not configured.
    """

    if settings.iyzico_webhook_secret is None:
        # Operator misconfiguration — refuse rather than silently accept
        # unsigned requests. 503 not 500: the signal is "this route is
        # configured but the secret is missing", not a code crash.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IYZICO_WEBHOOK_SECRET not configured",
        )

    raw_body = await request.body()

    try:
        iyzico.verify_signature(
            body=raw_body,
            secret=settings.iyzico_webhook_secret.get_secret_value(),
            header_value=signature,
        )
    except iyzico.InvalidSignatureError:
        logger.warning(
            "iyzico_webhook_invalid_signature",
            extra={"body_bytes": len(raw_body)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signature",
        ) from None

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body must be a JSON object",
        )

    try:
        event = iyzico.parse_event(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    tenant_id = await _resolve_tenant_by_customer_code(
        conn, customer_code=event.customer_reference_code
    )

    inserted = await iyzico.record_event(
        conn, tenant_id=tenant_id, event=event
    )

    plan = None
    if tenant_id is not None:
        plan = await iyzico.apply_plan_update(
            conn, tenant_id=tenant_id, event=event
        )

    logger.info(
        "iyzico_webhook_processed",
        extra={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "inserted": inserted,
            "plan_applied": plan.name if plan else None,
        },
    )
    return {"received": True}


async def _resolve_tenant_by_customer_code(
    conn: asyncpg.Connection, *, customer_code: str | None
) -> UUID | None:
    """Map ``customerReferenceCode`` → ``tenants.id`` or return None.

    Returning None is normal during onboarding — the very first event
    (``customer.created``) might arrive before our backend has wired
    the customer code into ``tenants.iyzico_customer_id``. The receiver
    persists what it can and the operator (or a follow-up event) fills
    in the gap.
    """

    if not customer_code:
        return None
    row = await conn.fetchrow(
        "select id from tenants where iyzico_customer_id = $1", customer_code
    )
    if row is None:
        return None
    return UUID(str(row["id"]))


# ── Outbound: hosted-checkout + cancel ────────────────────────────────


class CheckoutRequest(BaseModel):
    """``POST /checkout`` body — only the plan code is required.

    The tenant + caller email come from the JWT-authenticated session;
    customer name fields fall back to placeholders the FE can later
    enrich via ``PATCH /me`` (out of scope for this PR).
    """

    model_config = ConfigDict(extra="forbid")

    plan_reference_code: str = Field(min_length=1, max_length=128)


class CheckoutResponse(BaseModel):
    """``POST /checkout`` 200 — the FE redirects to ``payment_page_url``."""

    model_config = ConfigDict(frozen=True)

    payment_page_url: str
    token: str
    conversation_id: str


class CancelResponse(BaseModel):
    """``DELETE /subscription`` 200."""

    model_config = ConfigDict(frozen=True)

    status: str
    subscription_reference_code: str


def _require_iyzico_credentials(settings: Any) -> tuple[str, str]:
    """Return (api_key, secret_key) or raise 503.

    Outbound endpoints refuse with 503 (rather than 500) when keys are
    missing — operators see the misconfiguration distinctly from a
    code crash.
    """

    if settings.iyzico_api_key is None or settings.iyzico_secret_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IYZICO_API_KEY / IYZICO_SECRET_KEY not configured",
        )
    return (
        settings.iyzico_api_key.get_secret_value(),
        settings.iyzico_secret_key.get_secret_value(),
    )


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    summary="Initialise an Iyzico hosted-checkout session",
)
async def create_checkout(
    user_id: CurrentUserId,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: CheckoutRequest,
) -> CheckoutResponse:
    """Member-or-above creates a checkout session for an upgrade.

    Status codes:
    - 200: ``payment_page_url`` returned — FE redirects user.
    - 403: caller has no tenant (rare; auth identity gone).
    - 502: Iyzico returned a non-2xx (network / API error).
    - 503: Iyzico keys not configured.
    """

    api_key, secret_key = _require_iyzico_credentials(settings)
    tenant_id, _role = await resolve_tenant_and_role(conn, user_id=user_id)
    # Member is enough — any logged-in member can request an upgrade.
    # Cancel below requires admin so this stays asymmetric on purpose.

    customer = await conn.fetchrow(
        """
        select au.email, u.display_name
          from public.users u
          join auth.users au on au.id = u.id
         where u.id = $1
        """,
        user_id,
    )
    if customer is None or not customer["email"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="caller has no email on file",
        )

    display = (customer["display_name"] or "Bluedev User").split(" ", 1)
    first = display[0]
    last = display[1] if len(display) > 1 else "Customer"

    async with IyzicoClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=settings.iyzico_base_url,
    ) as client:
        try:
            session = await client.create_subscription_checkout(
                plan_reference_code=body.plan_reference_code,
                tenant_id=tenant_id,
                customer_email=str(customer["email"]),
                customer_first_name=first,
                customer_last_name=last,
                callback_url=settings.iyzico_callback_url,
            )
        except IyzicoOutboundError as exc:
            logger.warning(
                "iyzico_checkout_failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "status_code": exc.status_code,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Iyzico returned {exc.status_code}",
            ) from exc

    await write_audit_event(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="tenant.checkout_initiated",
        resource_type="tenant",
        resource_id=tenant_id,
        diff={"plan_reference_code": body.plan_reference_code},
    )

    logger.info(
        "iyzico_checkout_initiated",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "plan_reference_code": body.plan_reference_code,
        },
    )
    return CheckoutResponse(
        payment_page_url=session.payment_page_url,
        token=session.token,
        conversation_id=session.conversation_id,
    )


@router.delete(
    "/subscription",
    response_model=CancelResponse,
    summary="Cancel the tenant's active Iyzico subscription (admin only)",
)
async def cancel_subscription(
    user_id: CurrentUserId,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> CancelResponse:
    """Admin or owner cancels the active subscription.

    The endpoint reads ``tenants.iyzico_subscription_reference``, calls
    Iyzico, and audits. The actual ``tenants.plan`` downgrade happens
    when the eventual webhook arrives — kept asymmetric on purpose so
    we have one source of truth (the webhook).

    Status codes:
    - 200: Iyzico accepted the cancel.
    - 403: caller is not owner/admin.
    - 404: tenant has no active subscription.
    - 502: Iyzico returned a non-2xx.
    - 503: Iyzico keys not configured.
    """

    api_key, secret_key = _require_iyzico_credentials(settings)
    tenant_id, role = await resolve_tenant_and_role(conn, user_id=user_id)
    require_admin(role, action="cancel subscription")

    subscription_ref = await conn.fetchval(
        "select iyzico_subscription_reference from tenants where id = $1",
        tenant_id,
    )
    if not subscription_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tenant has no active subscription",
        )

    async with IyzicoClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=settings.iyzico_base_url,
    ) as client:
        try:
            cancel = await client.cancel_subscription(
                subscription_reference_code=str(subscription_ref)
            )
        except IyzicoOutboundError as exc:
            logger.warning(
                "iyzico_cancel_failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "status_code": exc.status_code,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Iyzico returned {exc.status_code}",
            ) from exc

    await write_audit_event(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="tenant.subscription_cancelled",
        resource_type="tenant",
        resource_id=tenant_id,
        diff={"event": "cancelled"},
    )

    logger.info(
        "iyzico_subscription_cancel_requested",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
        },
    )
    return CancelResponse(
        status=cancel.status,
        subscription_reference_code=cancel.subscription_reference_code,
    )


__all__ = ["router"]
