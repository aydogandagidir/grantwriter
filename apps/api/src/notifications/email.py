"""Resend-backed transactional email sender — lazy, optional, scrubbed.

The shape mirrors :mod:`src.core.observability`:

- ``import resend`` is deferred into :func:`_send_via_resend`. An env
  without the SDK installed (CI fast lane, dev laptops) does not raise
  on import — it just returns ``SendResult(status="skipped",
  reason="resend not installed")``.
- A missing ``RESEND_API_KEY`` is treated identically to a missing
  package; ``EMAIL_ENABLED=false`` is the kill-switch that trumps both.
- Send-failures NEVER log the rendered body. The structured log line
  carries ``template_name``, ``recipient_count``, and a status — never
  HTML, never the API response body.

The ``send_*`` helpers are convenience wrappers that render the
template + ship it. Tests stub :func:`_send_via_resend` directly to
assert call args without needing the real SDK.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from src.core.config import Settings, get_settings
from src.core.observability import _scrub_string
from src.notifications.suppression import is_recipient_suppressed
from src.notifications.templates import (
    EmailPayload,
    render_draft_complete_email,
    render_invitation_email,
    render_member_added_email,
)

logger = logging.getLogger(__name__)


SendStatus = Literal["sent", "skipped", "failed"]


@dataclass(frozen=True)
class SendResult:
    """What happened to one send call.

    ``status="skipped"`` is the no-op path: kill-switch off, key absent,
    or the SDK isn't installed. Caller treats skipped + sent as
    success; only ``status="failed"`` warrants an audit row.
    """

    status: SendStatus
    template_name: str
    reason: str | None = None
    provider_message_id: str | None = None


# ── Internal: scrub + send ────────────────────────────────────────────


def _scrub_payload(payload: EmailPayload) -> EmailPayload:
    """Run the observability scrubber over every body field.

    Belt-and-suspenders against a future caller embedding a BYOK key
    or JWT in a template variable. The scrubber is a pure function so
    rebuilding a frozen :class:`EmailPayload` here is cheap.
    """

    return EmailPayload(
        to=payload.to,
        subject=_scrub_string(payload.subject),
        html=_scrub_string(payload.html),
        plain=_scrub_string(payload.plain),
        template_name=payload.template_name,
        idempotency_key=payload.idempotency_key,
        lang=payload.lang,
    )


async def _send_via_resend(
    payload: EmailPayload,
    *,
    settings: Settings | None = None,
    conn: asyncpg.Connection | None = None,
) -> SendResult:
    """The single Resend call surface.

    Tests monkeypatch this function rather than the SDK so the test
    surface stays under our control. The real implementation lazy-
    imports ``resend`` and calls ``Emails.send`` synchronously inside
    a thread (the SDK is sync; the rest of the app is async).
    """

    cfg = settings or get_settings()

    # Suppression list — applied BEFORE the Resend round-trip so we
    # neither hit the SDK nor waste a paid send on a known-bad address.
    # ``conn`` is optional: callers without a DB connection get the
    # old behaviour. Best-effort: any failure in the suppression query
    # logs + lets the send proceed (a DB hiccup must not mute every
    # email).
    if conn is not None:
        try:
            suppressed = await is_recipient_suppressed(
                conn, recipient=str(payload.to)
            )
        except Exception:
            logger.exception(
                "email_suppression_check_failed",
                extra={"template": payload.template_name},
            )
            suppressed = False
        if suppressed:
            logger.info(
                "email_skipped",
                extra={
                    "template": payload.template_name,
                    "reason": "recipient_suppressed",
                    "recipient_count": 1,
                },
            )
            return SendResult(
                status="skipped",
                template_name=payload.template_name,
                reason="recipient_suppressed",
            )

    if not cfg.email_enabled:
        logger.info(
            "email_skipped",
            extra={
                "template": payload.template_name,
                "reason": "EMAIL_ENABLED=false",
                "recipient_count": 1,
            },
        )
        return SendResult(
            status="skipped",
            template_name=payload.template_name,
            reason="EMAIL_ENABLED=false",
        )

    if cfg.resend_api_key is None:
        logger.info(
            "email_skipped",
            extra={
                "template": payload.template_name,
                "reason": "RESEND_API_KEY not configured",
                "recipient_count": 1,
            },
        )
        return SendResult(
            status="skipped",
            template_name=payload.template_name,
            reason="RESEND_API_KEY not configured",
        )

    try:
        import resend
    except ImportError:
        logger.info(
            "email_skipped",
            extra={
                "template": payload.template_name,
                "reason": "resend not installed",
                "recipient_count": 1,
            },
        )
        return SendResult(
            status="skipped",
            template_name=payload.template_name,
            reason="resend not installed",
        )

    scrubbed = _scrub_payload(payload)
    body: dict[str, Any] = {
        "from": cfg.email_from,
        "to": [str(scrubbed.to)],
        "subject": scrubbed.subject,
        "html": scrubbed.html,
        "text": scrubbed.plain,
        "headers": {"Idempotency-Key": scrubbed.idempotency_key},
    }

    resend.api_key = cfg.resend_api_key.get_secret_value()
    try:
        response = await _run_in_thread(resend.Emails.send, body)
    except Exception as exc:
        logger.warning(
            "email_send_failed",
            extra={
                "template": payload.template_name,
                "recipient_count": 1,
                "error_class": type(exc).__name__,
            },
        )
        return SendResult(
            status="failed",
            template_name=payload.template_name,
            reason=f"send raised: {type(exc).__name__}",
        )

    message_id = None
    if isinstance(response, dict):
        message_id = response.get("id")

    logger.info(
        "email_sent",
        extra={
            "template": payload.template_name,
            "recipient_count": 1,
            "provider_message_id": message_id,
        },
    )
    return SendResult(
        status="sent",
        template_name=payload.template_name,
        provider_message_id=str(message_id) if message_id else None,
    )


async def _run_in_thread(fn: Any, *args: Any) -> Any:
    """Bridge the sync Resend SDK into an async caller.

    Resend's Python SDK does its HTTP via ``requests`` — calling it
    from an event loop blocks the loop. The wrapper hands the call to
    the default executor; tests monkeypatch :func:`_send_via_resend`
    above this layer so they don't need the executor either.
    """

    import asyncio

    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


# ── Public send helpers ───────────────────────────────────────────────


async def send_invitation_email(
    *,
    to: str,
    accept_url: str,
    inviter_name: str | None,
    tenant_name: str,
    role: str,
    expires_at: datetime,
    invitation_id: UUID,
    lang: str | None = None,
    settings: Settings | None = None,
    conn: asyncpg.Connection | None = None,
) -> SendResult:
    """Render + send the invitation template.

    The accept URL composition is the caller's responsibility (the
    invitations route owns the routing convention); the template just
    renders the link verbatim. Passing ``conn`` enables the bounce /
    complaint suppression check before the Resend call.
    """

    payload = render_invitation_email(
        to=to,
        accept_url=accept_url,
        inviter_name=inviter_name,
        tenant_name=tenant_name,
        role=role,
        expires_at=expires_at,
        invitation_id=invitation_id,
        lang=lang,
    )
    return await _send_via_resend(payload, settings=settings, conn=conn)


async def send_draft_complete_email(
    *,
    to: str,
    proposal_id: UUID,
    proposal_title: str,
    proposal_url: str,
    status: str,
    has_blockers: bool,
    lang: str | None = None,
    settings: Settings | None = None,
    conn: asyncpg.Connection | None = None,
) -> SendResult:
    """Saga-complete notification for the proposal owner."""

    payload = render_draft_complete_email(
        to=to,
        proposal_id=proposal_id,
        proposal_title=proposal_title,
        proposal_url=proposal_url,
        status=status,
        has_blockers=has_blockers,
        lang=lang,
    )
    return await _send_via_resend(payload, settings=settings, conn=conn)


async def send_member_added_email(
    *,
    to: str,
    new_member_email: str,
    new_member_role: str,
    tenant_name: str,
    invitation_id: UUID,
    lang: str | None = None,
    settings: Settings | None = None,
    conn: asyncpg.Connection | None = None,
) -> SendResult:
    """Owner-facing notification when an invitee accepts."""

    payload = render_member_added_email(
        to=to,
        new_member_email=new_member_email,
        new_member_role=new_member_role,
        tenant_name=tenant_name,
        invitation_id=invitation_id,
        lang=lang,
    )
    return await _send_via_resend(payload, settings=settings, conn=conn)


__all__ = [
    "SendResult",
    "SendStatus",
    "send_draft_complete_email",
    "send_invitation_email",
    "send_member_added_email",
]
