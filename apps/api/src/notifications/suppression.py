"""Recipient suppression — don't email known hard-bouncers / complainers.

Reads ``email_events`` (populated by the Resend webhook receiver) for
the most recent event per recipient. A recipient is suppressed when
their latest event is:

- ``email.complained`` (the user marked a previous email as spam — we
  must never email them again from this domain, or our reputation
  craters), or
- ``email.bounced`` with ``data.bounce.type == 'hard'`` (mailbox
  doesn't exist / domain is dead — soft bounces, e.g. "mailbox full",
  are NOT suppressed; the next send will likely deliver).

The check is idempotent + cheap (covered by the
``idx_email_events_recipient`` index on
``(recipient, received_at desc)``). Sender wrappers call it
optionally — when no conn is provided the check is skipped, which
matches the "best-effort email" contract (a missing DB never breaks
the parent flow).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


_SUPPRESSED_EVENT_TYPES = ("email.bounced", "email.complained")


def _is_hard_bounce_payload(payload: Any) -> bool:
    """Inspect the persisted payload jsonb for ``data.bounce.type == 'hard'``."""

    decoded = payload
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(decoded, dict):
        return False
    data = decoded.get("data")
    if not isinstance(data, dict):
        return False
    bounce = data.get("bounce")
    if not isinstance(bounce, dict):
        return False
    return str(bounce.get("type", "")).lower() == "hard"


async def is_recipient_suppressed(
    conn: asyncpg.Connection, *, recipient: str
) -> bool:
    """True iff the recipient's most recent webhook event suppresses sending.

    Looks at the single most recent ``email.bounced`` / ``email.complained``
    row. We don't aggregate older soft bounces — if Resend records a
    successful ``email.delivered`` between bad events the recipient is
    fine again.
    """

    if not recipient:
        return False

    row = await conn.fetchrow(
        """
        select event_type, payload::text as payload_text
          from email_events
         where recipient = $1
           and event_type = any($2::text[])
         order by received_at desc
         limit 1
        """,
        recipient,
        list(_SUPPRESSED_EVENT_TYPES),
    )
    if row is None:
        return False

    event_type = str(row["event_type"])
    if event_type == "email.complained":
        return True
    if event_type == "email.bounced":
        return _is_hard_bounce_payload(row["payload_text"])
    return False


__all__ = ["is_recipient_suppressed"]
