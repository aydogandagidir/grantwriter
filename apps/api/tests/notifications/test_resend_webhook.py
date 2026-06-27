"""Tests for the Resend webhook receiver + Svix signature verifier.

Covers:
- Pure Svix HMAC: correct sig accepts, tampered body / mismatched
  secret / missing header / out-of-skew timestamp all raise.
- The ``whsec_`` + base64 secret-prefix handling (operators paste the
  full dashboard value verbatim).
- Route: 503 without secret; 401 missing or wrong signature; 400 for
  invalid JSON; 200 + persisted row on success; replayed event is
  idempotent (200 again, no extra row).

DB-bound tests skip when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.config import get_settings
from src.core.db import get_db
from src.main import create_app
from src.notifications import resend_webhook


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# Operator-side secret is ``whsec_<base64>`` — the bytes after the
# prefix are the HMAC key. We pick a small random key for tests.
_RAW_KEY = b"resend-test-key-32-bytes-padding"
_WEBHOOK_SECRET = "whsec_" + base64.b64encode(_RAW_KEY).decode("ascii")


# ── Pure HMAC unit tests (no DB) ──────────────────────────────────────


def _signed(
    payload: dict[str, object],
    *,
    secret: str = _WEBHOOK_SECRET,
    message_id: str = "msg_abc",
    timestamp: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    raw = json.dumps(payload).encode("utf-8")
    sig = resend_webhook.compute_signature(
        secret=secret, message_id=message_id, timestamp=ts, raw_body=raw
    )
    return raw, {
        "svix-id": message_id,
        "svix-timestamp": ts,
        "svix-signature": sig,
    }


def test_verify_signature_accepts_a_correctly_signed_body() -> None:
    body = b'{"type":"email.delivered","data":{}}'
    ts = str(int(time.time()))
    sig = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=ts, raw_body=body
    )
    resend_webhook.verify_signature(
        secret=_WEBHOOK_SECRET,
        message_id="msg_1",
        timestamp=ts,
        signature_header=sig,
        raw_body=body,
    )


def test_verify_signature_rejects_a_tampered_body() -> None:
    body = b'{"type":"email.delivered"}'
    ts = str(int(time.time()))
    sig = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=ts, raw_body=body
    )
    with pytest.raises(resend_webhook.InvalidSignatureError):
        resend_webhook.verify_signature(
            secret=_WEBHOOK_SECRET,
            message_id="msg_1",
            timestamp=ts,
            signature_header=sig,
            raw_body=body + b"x",
        )


def test_verify_signature_rejects_a_wrong_secret() -> None:
    body = b'{}'
    ts = str(int(time.time()))
    sig = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=ts, raw_body=body
    )
    other = "whsec_" + base64.b64encode(b"a-different-32-bytes-padding-aaaa").decode("ascii")
    with pytest.raises(resend_webhook.InvalidSignatureError):
        resend_webhook.verify_signature(
            secret=other,
            message_id="msg_1",
            timestamp=ts,
            signature_header=sig,
            raw_body=body,
        )


def test_verify_signature_rejects_missing_headers() -> None:
    body = b'{}'
    with pytest.raises(resend_webhook.InvalidSignatureError):
        resend_webhook.verify_signature(
            secret=_WEBHOOK_SECRET,
            message_id=None,
            timestamp=str(int(time.time())),
            signature_header="v1,whatever",
            raw_body=body,
        )


def test_verify_signature_rejects_timestamps_outside_skew_window() -> None:
    body = b'{}'
    now = time.time()
    too_old = str(int(now - 600))  # 10 minutes ago
    sig = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=too_old, raw_body=body
    )
    with pytest.raises(resend_webhook.InvalidSignatureError):
        resend_webhook.verify_signature(
            secret=_WEBHOOK_SECRET,
            message_id="msg_1",
            timestamp=too_old,
            signature_header=sig,
            raw_body=body,
            now=now,
        )


def test_verify_signature_accepts_one_of_multiple_versions() -> None:
    """Svix supports key-rotation by sending multiple signatures
    separated by spaces. Any match should pass."""

    body = b'{}'
    ts = str(int(time.time()))
    real = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=ts, raw_body=body
    )
    composite = f"v1,definitely-not-real {real}"
    resend_webhook.verify_signature(
        secret=_WEBHOOK_SECRET,
        message_id="msg_1",
        timestamp=ts,
        signature_header=composite,
        raw_body=body,
    )


# ── Route: error paths (no DB needed) ──────────────────────────────────


def _build_client(*, pool: asyncpg.Pool | None) -> tuple[FastAPI, AsyncClient]:
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        if pool is None:
            # The error-path tests don't write to the DB. Yield a
            # bare-minimum stub instead of acquiring a real connection
            # so the dependency resolves before our settings / sig
            # checks fire (those return 503 / 401 first).
            class _NoopConn:
                async def execute(self, *_args: object, **_kwargs: object) -> None:
                    return None

                async def fetchval(self, *_args: object, **_kwargs: object) -> None:
                    return None

                async def fetchrow(self, *_args: object, **_kwargs: object) -> None:
                    return None

            yield _NoopConn()  # type: ignore[misc]
            return
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def _webhook_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    get_settings.cache_clear()
    return _WEBHOOK_SECRET


async def test_route_returns_503_when_secret_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()

    _app, client = _build_client(pool=None)
    async with client:
        response = await client.post(
            "/api/v1/notifications/resend-webhook", content=b'{}'
        )
    assert response.status_code == 503


async def test_route_returns_401_for_missing_signature(_webhook_env: str) -> None:
    _app, client = _build_client(pool=None)
    async with client:
        response = await client.post(
            "/api/v1/notifications/resend-webhook",
            content=b'{"type":"email.delivered"}',
        )
    assert response.status_code == 401


async def test_route_returns_401_for_wrong_signature(_webhook_env: str) -> None:
    _app, client = _build_client(pool=None)
    async with client:
        response = await client.post(
            "/api/v1/notifications/resend-webhook",
            content=b'{"type":"email.delivered","data":{}}',
            headers={
                "svix-id": "msg_1",
                "svix-timestamp": str(int(time.time())),
                "svix-signature": "v1,definitely-not-real",
            },
        )
    assert response.status_code == 401


# ── Route: DB-bound happy path + idempotency ───────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound Resend webhook tests"
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


async def _cleanup(pool: asyncpg.Pool, event_ids: list[str]) -> None:
    if not event_ids:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from email_events where provider_event_id = any($1::text[])",
            event_ids,
        )


async def test_route_persists_event_and_is_idempotent_on_replay(
    pool: asyncpg.Pool, _webhook_env: str
) -> None:
    event_id = f"evt-{uuid.uuid4()}"
    payload = {
        "id": event_id,
        "type": "email.delivered",
        "data": {
            "email_id": "msg_42",
            "to": ["recipient@example.com"],
        },
    }
    raw, headers = _signed(payload)
    try:
        _app, client = _build_client(pool=pool)
        async with client:
            first = await client.post(
                "/api/v1/notifications/resend-webhook",
                content=raw,
                headers=headers,
            )
            second = await client.post(
                "/api/v1/notifications/resend-webhook",
                content=raw,
                headers=headers,
            )

        assert first.status_code == 200
        assert second.status_code == 200

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "select count(*) from email_events where provider_event_id = $1",
                event_id,
            )
            row = await conn.fetchrow(
                "select event_type, recipient, message_id from email_events "
                "where provider_event_id = $1",
                event_id,
            )
        assert count == 1
        assert row["event_type"] == "email.delivered"
        assert row["recipient"] == "recipient@example.com"
        assert row["message_id"] == "msg_42"
    finally:
        await _cleanup(pool, [event_id])


async def test_route_400s_invalid_json_body(
    pool: asyncpg.Pool, _webhook_env: str
) -> None:
    """The body is signed, but the bytes don't parse as JSON."""

    body = b'{this is not json'
    ts = str(int(time.time()))
    sig = resend_webhook.compute_signature(
        secret=_WEBHOOK_SECRET, message_id="msg_1", timestamp=ts, raw_body=body
    )
    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/notifications/resend-webhook",
            content=body,
            headers={
                "svix-id": "msg_1",
                "svix-timestamp": ts,
                "svix-signature": sig,
            },
        )
    assert response.status_code == 400
