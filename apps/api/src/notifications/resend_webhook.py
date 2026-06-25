"""Resend webhook signature verification + event persistence.

Resend ships Svix-style webhook signatures: every POST carries
``svix-id``, ``svix-timestamp``, and ``svix-signature`` headers, where
the signature is HMAC-SHA256 of ``{id}.{timestamp}.{raw_body}``
base64-encoded. The header value can contain multiple sig versions
space-separated; we accept any of them so a key rotation doesn't
break in-flight deliveries.

We intentionally don't use the ``svix`` SDK — it adds ~6 transitive
deps for one HMAC + a JSON parse. The helpers below stay pure-stdlib
so a webhook regression doesn't require an SDK upgrade.

Replay protection: the timestamp must be within a 5-minute skew
window. Older / newer payloads are rejected as a defence against
log-replay attacks (operator pastes a captured request body into a
test tool weeks later).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


# Headers Resend / Svix sends with every webhook.
ID_HEADER = "svix-id"
TIMESTAMP_HEADER = "svix-timestamp"
SIGNATURE_HEADER = "svix-signature"

# Skew window for the timestamp check (seconds either side of now).
_MAX_SKEW_SECONDS = 300

# Resend prefixes its webhook-signing keys with ``whsec_``; strip it so
# the HMAC sees the raw bytes the dashboard sourced.
_KEY_PREFIX = "whsec_"


class InvalidSignatureError(Exception):
    """Bad signature, missing header, or timestamp outside the skew window."""


# ── Signature ──────────────────────────────────────────────────────────


def _decode_secret(raw: str) -> bytes:
    """Strip the ``whsec_`` prefix + base64-decode the secret.

    Svix secrets land in the dashboard as ``whsec_<base64>``; the HMAC
    key is the decoded body. Callers pass the full string and we
    normalise here so the route signature stays simple.
    """

    body = raw.removeprefix(_KEY_PREFIX) if raw.startswith(_KEY_PREFIX) else raw
    try:
        return base64.b64decode(body)
    except Exception as exc:  # pragma: no cover — caught by verify_signature
        raise InvalidSignatureError("invalid secret encoding") from exc


def compute_signature(
    *,
    secret: str,
    message_id: str,
    timestamp: str,
    raw_body: bytes,
) -> str:
    """Build the ``v1,<base64-hmac>`` token Resend sends.

    Exposed so tests can sign synthetic payloads without re-implementing
    the algorithm. The format mirrors Svix's docs exactly.
    """

    payload = f"{message_id}.{timestamp}.{raw_body.decode('utf-8')}"
    digest = hmac.new(_decode_secret(secret), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode('ascii')}"


def verify_signature(
    *,
    secret: str,
    message_id: str | None,
    timestamp: str | None,
    signature_header: str | None,
    raw_body: bytes,
    now: float | None = None,
) -> None:
    """Constant-time HMAC check + skew-window enforcement.

    Raises :class:`InvalidSignatureError` on any mismatch. The caller
    maps that to 401 with NO body distinction — never surface to the
    attacker which check failed.
    """

    if not (message_id and timestamp and signature_header):
        raise InvalidSignatureError("missing svix headers")

    # Reject obviously bad timestamps before we hash anything heavy.
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise InvalidSignatureError("invalid timestamp") from exc
    current = now if now is not None else time.time()
    if abs(current - ts) > _MAX_SKEW_SECONDS:
        raise InvalidSignatureError("timestamp outside the skew window")

    expected = compute_signature(
        secret=secret,
        message_id=message_id,
        timestamp=timestamp,
        raw_body=raw_body,
    )

    # The header can carry multiple "v1,<sig>" entries separated by
    # spaces during key rotation. Accept if any matches.
    for candidate in signature_header.split():
        if hmac.compare_digest(expected, candidate):
            return
    raise InvalidSignatureError("signature mismatch")


# ── Event persistence ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ResendEvent:
    """Minimal slice of the payload we persist + dispatch on."""

    event_id: str
    event_type: str
    recipient: str | None
    message_id: str | None
    raw: dict[str, Any]


def parse_event(payload: dict[str, Any]) -> ResendEvent:
    """Pull the fields the receiver actually uses from the body.

    Resend's payload shape:

        {
          "type": "email.delivered",
          "created_at": "...",
          "data": {
            "email_id": "...",
            "to": ["alice@example.com"],
            ...
          }
        }

    The ``email_id`` is what we tie back to our log entries. The
    ``data.to`` list usually has one recipient; we record the first.
    """

    event_type = payload.get("type")
    if not event_type:
        raise ValueError("payload missing ``type``")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("payload ``data`` must be an object")

    message_id = data.get("email_id")
    recipients_raw = data.get("to")
    recipient: str | None = None
    if isinstance(recipients_raw, list) and recipients_raw:
        first = recipients_raw[0]
        if isinstance(first, str):
            recipient = first

    # Svix puts the message_id on the wire under ``svix-id``; the
    # webhook body's ``email_id`` is the message id, but Resend doesn't
    # ship its own unique event id, so we use ``email_id-event_type`` as
    # a synthetic key when nothing else is available.
    event_id = (
        str(payload.get("id"))
        or (f"{message_id}-{event_type}" if message_id else "")
    )
    if not event_id:
        raise ValueError("payload has no event identifier")

    return ResendEvent(
        event_id=event_id,
        event_type=str(event_type),
        recipient=recipient,
        message_id=str(message_id) if message_id else None,
        raw=payload,
    )


async def record_event(
    conn: asyncpg.Connection, *, event: ResendEvent
) -> bool:
    """Idempotent insert. Returns ``True`` if a new row was created."""

    inserted = await conn.fetchval(
        """
        insert into email_events (
          provider, provider_event_id, event_type,
          recipient, message_id, payload
        ) values (
          'resend', $1, $2, $3, $4, $5::jsonb
        )
        on conflict (provider_event_id) do nothing
        returning id
        """,
        event.event_id,
        event.event_type,
        event.recipient,
        event.message_id,
        json.dumps(event.raw),
    )
    if inserted is not None:
        logger.info(
            "resend_event_recorded",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "recipient": event.recipient,
                "message_id": event.message_id,
            },
        )
    return inserted is not None


__all__ = [
    "ID_HEADER",
    "InvalidSignatureError",
    "ResendEvent",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "compute_signature",
    "parse_event",
    "record_event",
    "verify_signature",
]
