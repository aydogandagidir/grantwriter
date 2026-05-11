"""Bilingual (TR/EN) email rendering helpers.

Each public ``render_*`` function returns an :class:`EmailPayload` that
:func:`src.notifications.email._send_via_resend` ships to Resend.
Templates are intentionally simple: a subject line, an HTML body, and a
plain-text fallback. We do NOT build a full template engine — three
transactional emails do not justify Jinja2 etc.

Language selection: callers pass ``lang`` explicitly (``"tr"`` or
``"en"``); the orchestrator and route handlers source it from the
proposal / tenant record. When the lang is unknown we default to ``"en"``
so the recipient never receives a TR template by accident.

PII handling: rendered HTML is passed through
:func:`src.core.observability._scrub_string` inside :mod:`email` before
the SDK call — even if a caller accidentally embeds a BYOK key in the
payload, the wire payload is redacted. Templates here therefore stay
plain-string and never log their own bodies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Lang = Literal["tr", "en"]


class EmailPayload(BaseModel):
    """Resolved subject + bodies + recipient.

    Frozen so a template can be rendered once and passed around without
    fear of accidental mutation. The ``idempotency_key`` flows into the
    Resend ``Idempotency-Key`` header to avoid duplicate sends on retry.
    """

    model_config = ConfigDict(frozen=True)

    to: EmailStr
    subject: str
    html: str = Field(min_length=1)
    plain: str = Field(min_length=1)
    template_name: str
    idempotency_key: str
    lang: Lang


# ── Helpers ────────────────────────────────────────────────────────────


def _normalise_lang(lang: str | None) -> Lang:
    """Coerce a free-form lang hint to ``"tr"`` or ``"en"``.

    Anything that isn't a recognised TR variant falls back to English so
    we never accidentally ship a TR body to a caller who asked for
    French / German / unknown.
    """

    if lang and lang.lower() in {"tr", "tr-tr", "turkish"}:
        return "tr"
    return "en"


def _format_expiry(value: datetime, lang: Lang) -> str:
    """ISO date for plain text; long form for HTML — locale-light."""

    return value.strftime("%Y-%m-%d %H:%M UTC") if lang == "en" else value.strftime(
        "%d.%m.%Y %H:%M UTC"
    )


# ── Invitation ─────────────────────────────────────────────────────────


def render_invitation_email(
    *,
    to: str,
    accept_url: str,
    inviter_name: str | None,
    tenant_name: str,
    role: str,
    expires_at: datetime,
    invitation_id: UUID,
    lang: str | None = None,
) -> EmailPayload:
    """Build the invitation email payload.

    The accept URL is composed by the caller from ``settings.app_url``
    so this function never touches Settings directly — keeps it pure
    and trivially testable.
    """

    norm_lang = _normalise_lang(lang)
    inviter = inviter_name or ("davet eden" if norm_lang == "tr" else "your inviter")
    expiry_str = _format_expiry(expires_at, norm_lang)

    if norm_lang == "tr":
        subject = f"[Bluedev] {tenant_name} ekibine davetlisiniz"
        plain = (
            f"Merhaba,\n\n"
            f"{inviter}, sizi {tenant_name} ekibine '{role}' rolüyle davet etti.\n"
            f"Daveti kabul etmek için: {accept_url}\n\n"
            f"Davet {expiry_str} tarihinde sona erer.\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Merhaba,</p>"
            f"<p><strong>{inviter}</strong>, sizi <strong>{tenant_name}</strong> "
            f"ekibine '<code>{role}</code>' rolüyle davet etti.</p>"
            f"<p><a href=\"{accept_url}\">Daveti kabul et</a></p>"
            f"<p style=\"color:#666;font-size:12px\">Davet {expiry_str} tarihinde sona erer.</p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )
    else:
        subject = f"[Bluedev] You're invited to join {tenant_name}"
        plain = (
            f"Hello,\n\n"
            f"{inviter} invited you to join {tenant_name} as a '{role}'.\n"
            f"Accept the invitation: {accept_url}\n\n"
            f"This invitation expires at {expiry_str}.\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Hello,</p>"
            f"<p><strong>{inviter}</strong> invited you to join "
            f"<strong>{tenant_name}</strong> as a '<code>{role}</code>'.</p>"
            f"<p><a href=\"{accept_url}\">Accept invitation</a></p>"
            f"<p style=\"color:#666;font-size:12px\">This invitation expires at {expiry_str}.</p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )

    return EmailPayload(
        to=to,
        subject=subject,
        html=html,
        plain=plain,
        template_name="invitation",
        idempotency_key=f"invitation:{invitation_id}",
        lang=norm_lang,
    )


# ── Draft complete ─────────────────────────────────────────────────────


def render_draft_complete_email(
    *,
    to: str,
    proposal_id: UUID,
    proposal_title: str,
    proposal_url: str,
    status: str,
    has_blockers: bool,
    lang: str | None = None,
) -> EmailPayload:
    """Build the saga-complete notification.

    ``has_blockers=True`` → the saga finished with status
    ``draft_complete_with_issues`` (Hallucination Hunter flagged
    something). The body nudges the user to review before exporting.
    """

    norm_lang = _normalise_lang(lang)

    if norm_lang == "tr":
        if has_blockers:
            subject = f"[Bluedev] Taslağınız hazır — incelemeniz gereken uyarılar var ({proposal_title})"
            note = (
                "Hallucination Hunter veya Compliance Reviewer dikkat etmeniz gereken "
                "noktalar tespit etti — dışa aktarmadan önce raporu inceleyin."
            )
        else:
            subject = f"[Bluedev] Taslağınız hazır ({proposal_title})"
            note = "Tüm kontroller temiz — dışa aktarmaya hazır."
        plain = (
            f"Merhaba,\n\n"
            f"'{proposal_title}' adlı taslağınızın saga koşusu tamamlandı (durum: {status}).\n"
            f"{note}\n\n"
            f"Taslağı görüntüle: {proposal_url}\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Merhaba,</p>"
            f"<p><strong>{proposal_title}</strong> adlı taslağınızın saga koşusu tamamlandı "
            f"(durum: <code>{status}</code>).</p>"
            f"<p>{note}</p>"
            f"<p><a href=\"{proposal_url}\">Taslağı görüntüle</a></p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )
    else:
        if has_blockers:
            subject = f"[Bluedev] Draft ready — issues to review ({proposal_title})"
            note = (
                "Hallucination Hunter or Compliance Reviewer flagged items — "
                "review the report before exporting."
            )
        else:
            subject = f"[Bluedev] Draft ready ({proposal_title})"
            note = "All checks clean — ready to export."
        plain = (
            f"Hello,\n\n"
            f"Saga finished for your draft '{proposal_title}' (status: {status}).\n"
            f"{note}\n\n"
            f"Open draft: {proposal_url}\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Hello,</p>"
            f"<p>Saga finished for your draft <strong>{proposal_title}</strong> "
            f"(status: <code>{status}</code>).</p>"
            f"<p>{note}</p>"
            f"<p><a href=\"{proposal_url}\">Open draft</a></p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )

    return EmailPayload(
        to=to,
        subject=subject,
        html=html,
        plain=plain,
        template_name="draft_complete",
        idempotency_key=f"draft_complete:{proposal_id}",
        lang=norm_lang,
    )


# ── Member added ───────────────────────────────────────────────────────


def render_member_added_email(
    *,
    to: str,
    new_member_email: str,
    new_member_role: str,
    tenant_name: str,
    invitation_id: UUID,
    lang: str | None = None,
) -> EmailPayload:
    """Build the owner-facing notice when an invitee accepts.

    Sent only to tenant owners (admins are skipped to keep the volume
    low). The idempotency key derives from the invitation id so a
    re-fired hook doesn't double-send.
    """

    norm_lang = _normalise_lang(lang)

    if norm_lang == "tr":
        subject = f"[Bluedev] {tenant_name} ekibine yeni üye katıldı"
        plain = (
            f"Merhaba,\n\n"
            f"{new_member_email} adresi '{new_member_role}' rolüyle "
            f"{tenant_name} ekibine katıldı.\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Merhaba,</p>"
            f"<p><strong>{new_member_email}</strong> adresi "
            f"'<code>{new_member_role}</code>' rolüyle "
            f"<strong>{tenant_name}</strong> ekibine katıldı.</p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )
    else:
        subject = f"[Bluedev] New member joined {tenant_name}"
        plain = (
            f"Hello,\n\n"
            f"{new_member_email} just joined {tenant_name} as '{new_member_role}'.\n\n"
            f"Bluedev GrantWriter\n"
        )
        html = (
            f"<p>Hello,</p>"
            f"<p><strong>{new_member_email}</strong> just joined "
            f"<strong>{tenant_name}</strong> as '<code>{new_member_role}</code>'.</p>"
            f"<p style=\"color:#666;font-size:12px\">Bluedev GrantWriter</p>"
        )

    return EmailPayload(
        to=to,
        subject=subject,
        html=html,
        plain=plain,
        template_name="member_added",
        idempotency_key=f"member_added:{invitation_id}:{to}",
        lang=norm_lang,
    )


__all__ = [
    "EmailPayload",
    "Lang",
    "render_draft_complete_email",
    "render_invitation_email",
    "render_member_added_email",
]
