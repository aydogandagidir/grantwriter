"""Smoke tests for the public /health + /health/sentry-test endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.config import get_settings


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


async def test_health_returns_version(client: AsyncClient) -> None:
    response = await client.get("/health")

    body = response.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"]


# ── TICKET-003: Sentry smoke probe ──────────────────────────────────────


async def test_sentry_test_returns_skipped_when_dsn_unset(client: AsyncClient) -> None:
    """Without SENTRY_DSN the probe MUST return 200 + skipped, not raise.

    Otherwise every CI run that doesn't mount Sentry secrets would 500 on
    this URL and a curious operator hitting it from a staging shell would
    get a misleading error.
    """

    response = await client.get("/health/sentry-test")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "skipped", "reason": "SENTRY_DSN not configured"}


async def test_sentry_test_raises_when_dsn_set(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With SENTRY_DSN set the probe MUST raise (500) so the operator can
    confirm the event landed in Sentry.

    The global exception handler in main.py catches the RuntimeError and
    converts it to a 500 JSON response — we assert that surface. We need
    a fresh AsyncClient with ``raise_app_exceptions=False`` so the ASGI
    transport returns the 500 response instead of re-raising the
    underlying RuntimeError into the test (which is the default for
    debuggability).
    """

    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/0")
    get_settings.cache_clear()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/sentry-test")
    assert response.status_code == 500
    body = response.json()
    assert body == {"detail": "Internal server error"}
