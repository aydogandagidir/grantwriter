"""Transactional email notifications.

Three templates ship today:

- :func:`send_invitation_email` — invitee receives the accept-link.
- :func:`send_draft_complete_email` — proposal owner is told the saga
  finished and the draft is ready for review.
- :func:`send_member_added_email` — tenant owners are notified when a
  new member joins via invitation accept.

The :mod:`src.notifications.email` module is the single send seam — it
loads Resend lazily (mirroring :mod:`src.core.observability`'s pattern),
no-ops when the API key or SDK is missing, and runs the observability
PII scrubber over every payload before shipping. The :mod:`templates`
module owns the rendering surface; tests stub :func:`_send_via_resend`
rather than the templates.
"""

from src.notifications import resend_webhook
from src.notifications.email import (
    send_draft_complete_email,
    send_invitation_email,
    send_member_added_email,
)
from src.notifications.templates import (
    EmailPayload,
    render_draft_complete_email,
    render_invitation_email,
    render_member_added_email,
)

__all__ = [
    "EmailPayload",
    "render_draft_complete_email",
    "render_invitation_email",
    "render_member_added_email",
    "resend_webhook",
    "send_draft_complete_email",
    "send_invitation_email",
    "send_member_added_email",
]
