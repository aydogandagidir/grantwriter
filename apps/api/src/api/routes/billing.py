"""Iyzico webhook receiver — public endpoint, HMAC-gated.

``POST /api/v1/billing/iyzico-webhook``

The route is authentication-free (Iyzico calls it from outside the
authenticated zone) but HMAC-locked: the body must carry a valid
``X-Iyzico-Signature`` header signed with ``IYZICO_WEBHOOK_SECRET``.
On a bad / missing signature the response is ``401`` — no detail
about which check failed (don't help an attacker enumerate).

Flow per request:

1. Read raw body bytes (signature is over the bytes-on-the-wire — a
   re-serialised JSON dict would have a different signature).
2. Verify HMAC. Bad → 401, no DB write.
3. Parse the JSON event.
4. Look up tenant by ``customerReferenceCode`` →
   ``tenants.iyzico_customer_id``.
5. Persist the event (idempotent — Iyzico retries on transient
   failures and would dupe ``provider_event_id`` otherwise).
6. Dispatch plan-mutating events (subscription
   activated/created/cancelled).

Returns ``200 {"received": true}`` on success — Iyzico interprets any
non-2xx as "retry".
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.billing import iyzico
from src.core.config import SettingsDep
from src.core.db import get_db

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


__all__ = ["router"]
