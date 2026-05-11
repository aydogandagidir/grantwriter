"""Route-level 429 behaviour for the rate-limited endpoints.

The unit tests in ``tests/core/test_rate_limit.py`` exercise the limiter
directly. This file proves that:

- The dep factory wires correctly into a real FastAPI app and trips the
  limit at the documented threshold.
- The 429 response carries ``Retry-After``, ``X-RateLimit-Limit``,
  ``X-RateLimit-Remaining``, and a structured ``detail`` body.
- Successful responses carry ``X-RateLimit-*`` headers when the route
  calls :func:`attach_rate_limit_headers`.
- A different user is unaffected by the first user's burn — confirms
  identity scoping at the FastAPI dep layer.

Skips when ``TEST_REDIS_URL`` / ``REDIS_URL`` is unset (no broker → the
limiter fail-opens and the 429 path can't be exercised).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.core.config import get_settings
from src.core.rate_limit import LLM_CONFIG_TEST
from src.main import create_app


def _redis_url() -> str | None:
    return os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def redis_url() -> str:
    url = _redis_url()
    if not url:
        pytest.skip("TEST_REDIS_URL/REDIS_URL not set — cannot exercise 429 path")
    return url


@pytest.fixture
def _redis_env(monkeypatch: pytest.MonkeyPatch, redis_url: str) -> str:
    """Inject ``REDIS_URL`` into the cached Settings so the limiter wires up."""

    monkeypatch.setenv("REDIS_URL", redis_url)
    get_settings.cache_clear()
    return redis_url


def _build_app(*, user_id: uuid.UUID) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return app


@pytest.fixture
async def isolated_user_a(_redis_env: str) -> AsyncIterator[dict[str, Any]]:
    """Per-test unique user → fresh Redis bucket. Cleans the bucket on teardown
    so reruns of this test on the same Redis don't leak state."""

    user_id = uuid.uuid4()
    app = _build_app(user_id=user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield {"client": ac, "app": app, "user_id": user_id}

    # Teardown: drop this user's keys so the next run starts clean.
    import redis.asyncio as redis_async

    cleanup = redis_async.from_url(  # type: ignore[no-untyped-call]
        _redis_env, encoding="utf-8", decode_responses=False
    )
    try:
        async for key in cleanup.scan_iter(
            match=f"ratelimit:*:{user_id}".encode()
        ):
            await cleanup.delete(key)
    finally:
        await cleanup.aclose()


# ── Helpers ────────────────────────────────────────────────────────────


def _stub_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The /test endpoint requires LLM_MASTER_ENCRYPTION_KEY before it
    even gets to the rate-limit dep — set it so the 429 fires for the
    right reason (not 503 for a missing master key)."""

    monkeypatch.setenv("LLM_MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-padding!")
    get_settings.cache_clear()


# ── Tests ──────────────────────────────────────────────────────────────


async def test_test_endpoint_returns_429_after_5_calls(
    isolated_user_a: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_CONFIG_TEST is 5 / 60s. The 6th call must return 429 with all
    the standard rate-limit headers and a structured body."""

    _stub_master_key(monkeypatch)

    client = isolated_user_a["client"]

    # The /test handler runs the rate-limit dep BEFORE any DB / provider
    # call, so we don't need a real DB — patching get_db away keeps the
    # test focused on the rate-limit code path. Same for the master-key
    # decrypt: the 6th request never reaches it.
    async def _no_op_db() -> Any:
        # Generator that never yields — the rate-limit dep raises before
        # the route body runs. For the first 5 calls we WILL reach the
        # body, so we yield a stub conn that errors on use; the 5 success
        # paths are out of scope here, only the 429 path is.
        raise AssertionError("DB should not be touched in this test")
        yield  # pragma: no cover

    # We instead let the real DB dep run but patch ``ClaudeProvider`` so
    # the 5 success calls are cheap. To keep the test self-contained we
    # patch deeper: the route resolves the tenant via get_db, so we mock
    # asyncpg.Connection.fetchval to return None — turns the route into
    # a "no_key_set" 200 response. That keeps the rate-limiter's bucket
    # advancing without touching real infra.
    from src.core import db as db_module

    async def _fake_get_db() -> AsyncIterator[Any]:
        from unittest.mock import AsyncMock

        conn = AsyncMock()
        # ``_resolve_tenant_id`` does conn.fetchval(...) → tenant_id;
        # ``key_vault.get_byok_key`` does conn.fetchrow(...) → None.
        conn.fetchval.return_value = uuid.uuid4()
        conn.fetchrow.return_value = None
        yield conn

    isolated_user_a["app"].dependency_overrides[db_module.get_db] = _fake_get_db

    # First 5 calls succeed (200) and consume the bucket.
    for i in range(LLM_CONFIG_TEST.limit):
        response = await client.post("/api/v1/tenant/llm-config/test")
        assert response.status_code == 200, (
            f"call {i + 1} got {response.status_code}: {response.text}"
        )
        assert response.headers["X-RateLimit-Limit"] == str(LLM_CONFIG_TEST.limit)
        # The header from `attach_rate_limit_headers` reflects the post-
        # consume `remaining` count.
        assert int(response.headers["X-RateLimit-Remaining"]) == (
            LLM_CONFIG_TEST.limit - 1 - i
        )

    # The 6th call is rejected.
    response = await client.post("/api/v1/tenant/llm-config/test")
    assert response.status_code == 429
    body = response.json()
    assert body["detail"]["error"] == "rate_limit_exceeded"
    assert body["detail"]["rule"] == LLM_CONFIG_TEST.name
    assert body["detail"]["limit"] == LLM_CONFIG_TEST.limit
    assert body["detail"]["window_seconds"] == LLM_CONFIG_TEST.window_seconds

    assert response.headers["Retry-After"]
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Limit"] == str(LLM_CONFIG_TEST.limit)
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["X-RateLimit-Window"] == str(
        LLM_CONFIG_TEST.window_seconds
    )


async def test_separate_users_have_separate_buckets(
    _redis_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Burn user A's bucket; user B's first request still succeeds."""

    _stub_master_key(monkeypatch)

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    async def _fake_get_db() -> AsyncIterator[Any]:
        from unittest.mock import AsyncMock

        conn = AsyncMock()
        conn.fetchval.return_value = uuid.uuid4()
        conn.fetchrow.return_value = None
        yield conn

    from src.core import db as db_module

    app_a = _build_app(user_id=user_a)
    app_a.dependency_overrides[db_module.get_db] = _fake_get_db
    app_b = _build_app(user_id=user_b)
    app_b.dependency_overrides[db_module.get_db] = _fake_get_db

    transport_a = ASGITransport(app=app_a)
    transport_b = ASGITransport(app=app_b)

    try:
        async with AsyncClient(
            transport=transport_a, base_url="http://test"
        ) as client_a:
            for _ in range(LLM_CONFIG_TEST.limit):
                resp = await client_a.post("/api/v1/tenant/llm-config/test")
                assert resp.status_code == 200
            blocked = await client_a.post("/api/v1/tenant/llm-config/test")
            assert blocked.status_code == 429

        async with AsyncClient(
            transport=transport_b, base_url="http://test"
        ) as client_b:
            ok = await client_b.post("/api/v1/tenant/llm-config/test")
            assert ok.status_code == 200
            assert int(ok.headers["X-RateLimit-Remaining"]) == (
                LLM_CONFIG_TEST.limit - 1
            )
    finally:
        # Clean both users' Redis buckets.
        import redis.asyncio as redis_async

        cleanup = redis_async.from_url(  # type: ignore[no-untyped-call]
            _redis_env, encoding="utf-8", decode_responses=False
        )
        try:
            for user in (user_a, user_b):
                async for key in cleanup.scan_iter(
                    match=f"ratelimit:*:{user}".encode()
                ):
                    await cleanup.delete(key)
        finally:
            await cleanup.aclose()


async def test_no_redis_configured_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``REDIS_URL`` the limiter fail-opens and never returns 429.

    This is the dev / smoke-deploy mode — important to test so a future
    accidental "fail-closed" change is caught immediately.
    """

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("TEST_REDIS_URL", raising=False)
    _stub_master_key(monkeypatch)
    get_settings.cache_clear()

    user_id = uuid.uuid4()
    app = _build_app(user_id=user_id)

    async def _fake_get_db() -> AsyncIterator[Any]:
        from unittest.mock import AsyncMock

        conn = AsyncMock()
        conn.fetchval.return_value = uuid.uuid4()
        conn.fetchrow.return_value = None
        yield conn

    from src.core import db as db_module

    app.dependency_overrides[db_module.get_db] = _fake_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Fire well past the configured limit; every call should still be 200.
        for _ in range(LLM_CONFIG_TEST.limit + 3):
            resp = await client.post("/api/v1/tenant/llm-config/test")
            assert resp.status_code == 200
