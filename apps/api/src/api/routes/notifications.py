"""Resend webhook receiver.

``POST /api/v1/notifications/resend-webhook``

Public endpoint (Resend calls it from outside the authenticated zone)
but Svix-HMAC-locked: the body must carry valid ``svix-id`` /
``svix-timestamp`` / ``svix-signature`` headers signed with
``RESEND_WEBHOOK_SECRET``. Bad / missing signature → 401 with no body
distinction.

Flow per delivery:

1. Read the raw body (signature is over the bytes-on-the-wire).
2. Verify HMAC + replay-protect via the 5-minute skew window.
3. Parse the JSON event.
4. Persist idempotently (``provider_event_id`` UNIQUE → ON CONFLICT
   DO NOTHING).

Returns ``200 {"received": true}`` on success. Resend interprets any
non-2xx as "retry" and re-delivers, which is why the receiver MUST
stay idempotent — duplicates are normal.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.core.config import SettingsDep
from src.core.db import get_db
from src.notifications import resend_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.post(
    "/resend-webhook",
    summary="Resend webhook receiver (Svix-HMAC-authenticated)",
)
async def resend_webhook_endpoint(
    request: Request,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[
        str | None, Header(alias="svix-timestamp")
    ] = None,
    svix_signature: Annotated[
        str | None, Header(alias="svix-signature")
    ] = None,
) -> dict[str, Any]:
    """Verify + persist one Resend webhook delivery.

    Status codes:
    - 200: accepted (idempotent — duplicate event still 200).
    - 400: body wasn't valid JSON.
    - 401: bad / missing signature OR timestamp outside the skew window.
    - 503: ``RESEND_WEBHOOK_SECRET`` not configured.
    """

    if settings.resend_webhook_secret is None:
        # Operator misconfiguration — refuse rather than silently accept
        # unsigned requests. 503 not 500: the signal is "route exists,
        # secret missing", not a code crash.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RESEND_WEBHOOK_SECRET not configured",
        )

    raw_body = await request.body()

    try:
        resend_webhook.verify_signature(
            secret=settings.resend_webhook_secret.get_secret_value(),
            message_id=svix_id,
            timestamp=svix_timestamp,
            signature_header=svix_signature,
            raw_body=raw_body,
        )
    except resend_webhook.InvalidSignatureError:
        logger.warning(
            "resend_webhook_invalid_signature",
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
        event = resend_webhook.parse_event(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    inserted = await resend_webhook.record_event(conn, event=event)

    logger.info(
        "resend_webhook_processed",
        extra={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "recipient": event.recipient,
            "inserted": inserted,
        },
    )
    return {"received": True}


__all__ = ["router"]
