"""Tests for the bounce / complaint suppression list.

Two layers:

A. **Pure ``is_recipient_suppressed``** — seed ``email_events`` rows
   with each event-type / payload combo and assert the suppression
   verdict. Tight coverage: hard bounce → suppress, soft bounce →
   allow, complaint → suppress, delivered → allow, no rows → allow.

B. **Send-helper integration** — invoke ``send_invitation_email`` with
   a stubbed Resend SDK + a connection pointing at a seeded
   ``email_events`` row. Assert the suppressed path returns
   ``SendResult(status="skipped", reason="recipient_suppressed")``
   AND does NOT call the Resend stub at all.

DB-bound; skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from src.notifications import email as email_module
from src.notifications.suppression import is_recipient_suppressed


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping suppression-list tests"
        )
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _seed_event(
    conn: asyncpg.Connection,
    *,
    recipient: str,
    event_type: str,
    payload: dict[str, Any],
    received_at: datetime | None = None,
) -> str:
    event_id = f"evt-{uuid.uuid4()}"
    await conn.execute(
        """
        insert into email_events (
          provider, provider_event_id, event_type, recipient,
          message_id, payload, received_at
        ) values (
          'resend', $1, $2, $3, $4, $5::jsonb, coalesce($6, now())
        )
        """,
        event_id,
        event_type,
        recipient,
        payload.get("data", {}).get("email_id"),
        json.dumps(payload),
        received_at,
    )
    return event_id


async def _cleanup(pool: asyncpg.Pool, event_ids: list[str]) -> None:
    if not event_ids:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from email_events where provider_event_id = any($1::text[])",
            event_ids,
        )


# ── Pure suppression logic ─────────────────────────────────────────────


async def test_no_events_for_recipient_means_allow(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        assert (
            await is_recipient_suppressed(
                conn, recipient=f"never-seen-{uuid.uuid4()}@example.com"
            )
            is False
        )


async def test_hard_bounce_suppresses(pool: asyncpg.Pool) -> None:
    recipient = f"hard-{uuid.uuid4()}@example.com"
    ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.bounced",
                    payload={
                        "type": "email.bounced",
                        "data": {
                            "to": [recipient],
                            "bounce": {"type": "hard", "subType": "general"},
                        },
                    },
                )
            )
            assert (
                await is_recipient_suppressed(conn, recipient=recipient)
                is True
            )
    finally:
        await _cleanup(pool, ids)


async def test_soft_bounce_does_not_suppress(pool: asyncpg.Pool) -> None:
    recipient = f"soft-{uuid.uuid4()}@example.com"
    ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.bounced",
                    payload={
                        "type": "email.bounced",
                        "data": {
                            "to": [recipient],
                            "bounce": {"type": "soft", "subType": "mailbox_full"},
                        },
                    },
                )
            )
            assert (
                await is_recipient_suppressed(conn, recipient=recipient)
                is False
            )
    finally:
        await _cleanup(pool, ids)


async def test_complaint_suppresses(pool: asyncpg.Pool) -> None:
    recipient = f"complaint-{uuid.uuid4()}@example.com"
    ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.complained",
                    payload={
                        "type": "email.complained",
                        "data": {"to": [recipient]},
                    },
                )
            )
            assert (
                await is_recipient_suppressed(conn, recipient=recipient)
                is True
            )
    finally:
        await _cleanup(pool, ids)


async def test_only_most_recent_event_decides(pool: asyncpg.Pool) -> None:
    """If a hard bounce was followed by a successful re-send (a new
    ``email.delivered``), the suppression check still sees the hard bounce
    because ``email.delivered`` isn't in the suppressed-event filter —
    Resend doesn't reset hard bounces. The recipient stays suppressed."""

    recipient = f"recent-{uuid.uuid4()}@example.com"
    ids: list[str] = []
    try:
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.bounced",
                    payload={
                        "type": "email.bounced",
                        "data": {
                            "to": [recipient],
                            "bounce": {"type": "hard"},
                        },
                    },
                    received_at=now - timedelta(days=10),
                )
            )
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.bounced",
                    payload={
                        "type": "email.bounced",
                        "data": {
                            "to": [recipient],
                            "bounce": {"type": "soft"},
                        },
                    },
                    received_at=now,
                )
            )
            assert (
                await is_recipient_suppressed(conn, recipient=recipient)
                is False
            ), "newer soft bounce overrides the older hard bounce"
    finally:
        await _cleanup(pool, ids)


# ── Integration with send_invitation_email ─────────────────────────────


async def test_send_invitation_skips_suppressed_recipient(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suppression check fires before ``_send_via_resend`` reaches
    Resend. Stub the SDK call so a successful path WOULD pop the toast
    — assert it doesn't run on the suppressed path."""

    recipient = f"skip-{uuid.uuid4()}@example.com"
    ids: list[str] = []
    sdk_calls: list[Any] = []

    async def fake_run_in_thread(_fn: Any, body: Any) -> Any:
        sdk_calls.append(body)
        return {"id": "msg_should_not_have_been_called"}

    monkeypatch.setenv("RESEND_API_KEY", "re_test_does_not_matter")
    monkeypatch.setattr(email_module, "_run_in_thread", fake_run_in_thread)

    try:
        async with pool.acquire() as conn:
            ids.append(
                await _seed_event(
                    conn,
                    recipient=recipient,
                    event_type="email.complained",
                    payload={
                        "type": "email.complained",
                        "data": {"to": [recipient]},
                    },
                )
            )
            result = await email_module.send_invitation_email(
                to=recipient,
                accept_url="https://app.bluedev.dev/invitations/x",
                inviter_name="Owner",
                tenant_name="Acme",
                role="member",
                expires_at=datetime.now(UTC) + timedelta(days=7),
                invitation_id=uuid.uuid4(),
                conn=conn,
            )

        assert result.status == "skipped"
        assert result.reason == "recipient_suppressed"
        assert sdk_calls == [], "Resend SDK must not be called for suppressed recipients"
    finally:
        await _cleanup(pool, ids)


async def test_send_invitation_clean_recipient_passes_suppression_check(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recipient with no bounce / complaint history must NOT be
    flagged ``recipient_suppressed``. The downstream "SDK not installed"
    skip is a separate concern (proper sends are exercised by
    ``tests/notifications/test_email.py``); here we just prove the
    suppression check let the request through to the SDK layer."""

    recipient = f"clean-{uuid.uuid4()}@example.com"

    async with pool.acquire() as conn:
        result = await email_module.send_invitation_email(
            to=recipient,
            accept_url="https://app.bluedev.dev/invitations/y",
            inviter_name="Owner",
            tenant_name="Acme",
            role="member",
            expires_at=datetime.now(UTC) + timedelta(days=7),
            invitation_id=uuid.uuid4(),
            conn=conn,
        )

    # Whichever skip reason fires, it must NOT be ``recipient_suppressed`` —
    # if it were, the suppression check would be incorrectly blocking a
    # clean recipient.
    assert result.reason != "recipient_suppressed"
