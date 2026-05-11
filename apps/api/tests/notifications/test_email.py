"""Tests for the Resend lazy-init email service.

Three concerns mirror the observability test layout:

A. **Skip paths** — kill-switch off, missing API key, missing SDK all
   return ``SendResult(status="skipped")`` without raising.
B. **Send path** — when both the SDK is mocked-importable and the key
   is set, the wire payload carries the rendered template, the
   ``Idempotency-Key`` header, and a redacted body.
C. **Error path** — an exception from the SDK is caught, the structured
   log line carries no body, and ``SendResult.status="failed"``.

The Resend SDK is import-faked via ``sys.modules`` so the tests work in
environments where the real package isn't installed.
"""

from __future__ import annotations

import logging
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from pydantic import SecretStr
from src.core.config import Settings
from src.notifications.email import (
    SendResult,
    _send_via_resend,
    send_draft_complete_email,
    send_invitation_email,
    send_member_added_email,
)
from src.notifications.templates import render_invitation_email

# ── Fixtures ───────────────────────────────────────────────────────────


def _settings(**overrides: Any) -> Settings:
    """Build a Settings instance with email-relevant defaults overridden."""

    base = {
        "resend_api_key": None,
        "email_enabled": True,
        "email_from": "Bluedev GrantWriter <noreply@bluedev.dev>",
        "app_url": "https://app.bluedev.dev",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def fake_resend_module() -> Any:
    """Install a fake ``resend`` module on ``sys.modules`` for the test.

    The real SDK isn't a project dep — we fake the surface
    (``api_key`` attribute + ``Emails.send`` callable) so the lazy
    import in ``email.py`` resolves to our stub.
    """

    module = types.ModuleType("resend")
    module.api_key = ""  # type: ignore[attr-defined]

    class _Emails:
        last_call: ClassVar[dict[str, Any] | None] = None
        return_value: ClassVar[dict[str, Any]] = {"id": "msg_xyz"}
        side_effect: ClassVar[Exception | None] = None

        @classmethod
        def send(cls, body: dict[str, Any]) -> dict[str, Any]:
            cls.last_call = body
            if cls.side_effect is not None:
                raise cls.side_effect
            return cls.return_value

    module.Emails = _Emails  # type: ignore[attr-defined]

    sys.modules["resend"] = module
    try:
        yield module
    finally:
        # Don't leave the fake stuck on real environments.
        sys.modules.pop("resend", None)
        # Reset class state so successive tests don't leak.
        _Emails.last_call = None
        _Emails.side_effect = None
        _Emails.return_value = {"id": "msg_xyz"}


@pytest.fixture(autouse=True)
def _drop_real_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts without a cached ``resend`` import.

    Some tests use ``fake_resend_module`` to install a stub; the others
    rely on ``import resend`` raising ``ImportError``. Clearing the
    import cache before each test gives both paths a clean slate.
    """

    monkeypatch.delitem(sys.modules, "resend", raising=False)


# ── Skip paths (A) ────────────────────────────────────────────────────


async def test_skip_when_kill_switch_off(caplog: pytest.LogCaptureFixture) -> None:
    payload = render_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invitation_id=uuid.uuid4(),
    )
    settings = _settings(email_enabled=False)

    with caplog.at_level(logging.INFO):
        result = await _send_via_resend(payload, settings=settings)

    assert result == SendResult(
        status="skipped",
        template_name="invitation",
        reason="EMAIL_ENABLED=false",
    )
    skip_records = [r for r in caplog.records if r.message == "email_skipped"]
    assert skip_records, "expected an email_skipped log line"


async def test_skip_when_api_key_missing(caplog: pytest.LogCaptureFixture) -> None:
    payload = render_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invitation_id=uuid.uuid4(),
    )
    settings = _settings(resend_api_key=None, email_enabled=True)

    result = await _send_via_resend(payload, settings=settings)

    assert result.status == "skipped"
    assert result.reason == "RESEND_API_KEY not configured"


async def test_skip_when_sdk_missing(caplog: pytest.LogCaptureFixture) -> None:
    payload = render_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invitation_id=uuid.uuid4(),
    )
    settings = _settings(
        resend_api_key=SecretStr("re_test_xxx"), email_enabled=True
    )

    # No fake_resend_module fixture in this test — import resend will fail.
    result = await _send_via_resend(payload, settings=settings)

    assert result.status == "skipped"
    assert result.reason == "resend not installed"


# ── Send path (B) ──────────────────────────────────────────────────────


async def test_send_uses_idempotency_key_and_returns_message_id(
    fake_resend_module: Any,
) -> None:
    invitation_id = uuid.uuid4()
    settings = _settings(resend_api_key=SecretStr("re_test_xxx"))

    result = await send_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invitation_id=invitation_id,
        lang="en",
        settings=settings,
    )

    assert result.status == "sent"
    assert result.provider_message_id == "msg_xyz"

    body = fake_resend_module.Emails.last_call
    assert body is not None
    assert body["to"] == ["alice@example.com"]
    assert body["from"] == settings.email_from
    assert "Acme" in body["subject"]
    # Idempotency-Key flows in via headers.
    assert body["headers"]["Idempotency-Key"] == f"invitation:{invitation_id}"


async def test_scrubber_redacts_byok_key_in_body(
    fake_resend_module: Any,
) -> None:
    """Belt-and-suspenders: a BYOK key embedded in a template var is
    redacted before the wire payload is built."""

    invitation_id = uuid.uuid4()
    leaked_key = "sk-ant-" + ("a" * 60)
    settings = _settings(resend_api_key=SecretStr("re_test_xxx"))

    result = await send_invitation_email(
        to="alice@example.com",
        accept_url=f"https://app.bluedev.dev/invitations/{leaked_key}",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invitation_id=invitation_id,
        lang="en",
        settings=settings,
    )

    assert result.status == "sent"
    body = fake_resend_module.Emails.last_call
    assert body is not None
    assert leaked_key not in body["html"]
    assert leaked_key not in body["text"]
    assert "***REDACTED***" in body["html"]


async def test_invitation_tr_subject_differs_from_en(
    fake_resend_module: Any,
) -> None:
    settings = _settings(resend_api_key=SecretStr("re_test_xxx"))
    invitation_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=7)

    await send_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=expires_at,
        invitation_id=invitation_id,
        lang="tr",
        settings=settings,
    )
    tr_subject = fake_resend_module.Emails.last_call["subject"]
    tr_body = fake_resend_module.Emails.last_call["text"]

    await send_invitation_email(
        to="alice@example.com",
        accept_url="https://app.bluedev.dev/invitations/abc",
        inviter_name="Bob",
        tenant_name="Acme",
        role="member",
        expires_at=expires_at,
        invitation_id=invitation_id,
        lang="en",
        settings=settings,
    )
    en_subject = fake_resend_module.Emails.last_call["subject"]
    en_body = fake_resend_module.Emails.last_call["text"]

    assert "davetlisiniz" in tr_subject
    assert "invited" in en_subject
    assert tr_subject != en_subject
    assert tr_body != en_body


# ── Error path (C) ────────────────────────────────────────────────────


async def test_send_failure_returns_failed_without_body_in_log(
    fake_resend_module: Any, caplog: pytest.LogCaptureFixture
) -> None:
    fake_resend_module.Emails.side_effect = RuntimeError("forbidden token")
    settings = _settings(resend_api_key=SecretStr("re_test_xxx"))

    with caplog.at_level(logging.WARNING):
        result = await send_member_added_email(
            to="owner@example.com",
            new_member_email="invitee@example.com",
            new_member_role="member",
            tenant_name="Acme",
            invitation_id=uuid.uuid4(),
            settings=settings,
        )

    assert result.status == "failed"
    assert result.template_name == "member_added"
    assert "RuntimeError" in (result.reason or "")

    fail_records = [r for r in caplog.records if r.message == "email_send_failed"]
    assert fail_records, "expected an email_send_failed log line"
    record = fail_records[0]
    # Body must NOT appear in the log line — only metadata.
    assert "forbidden token" not in record.getMessage()
    assert getattr(record, "template", None) == "member_added"
    assert getattr(record, "error_class", None) == "RuntimeError"


async def test_draft_complete_blockers_changes_subject(
    fake_resend_module: Any,
) -> None:
    settings = _settings(resend_api_key=SecretStr("re_test_xxx"))
    proposal_id = uuid.uuid4()

    await send_draft_complete_email(
        to="owner@example.com",
        proposal_id=proposal_id,
        proposal_title="My HE Proposal",
        proposal_url="https://app.bluedev.dev/proposals/x",
        status="draft_complete",
        has_blockers=False,
        lang="en",
        settings=settings,
    )
    clean_subject = fake_resend_module.Emails.last_call["subject"]

    await send_draft_complete_email(
        to="owner@example.com",
        proposal_id=proposal_id,
        proposal_title="My HE Proposal",
        proposal_url="https://app.bluedev.dev/proposals/x",
        status="draft_complete_with_issues",
        has_blockers=True,
        lang="en",
        settings=settings,
    )
    blocked_subject = fake_resend_module.Emails.last_call["subject"]

    assert "issues to review" in blocked_subject
    assert clean_subject != blocked_subject
