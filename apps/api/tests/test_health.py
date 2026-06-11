"""Smoke tests for the public /health + /health/sentry-test endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


# ── /health/db + lifespan-resilience contract ───────────────────────────
#
# The lifespan in src/main.py is deliberately tolerant: a DB pool init
# failure must NOT crash boot. Instead, ``app.state.db_pool`` is left as
# ``None``, ``app.state.db_pool_init_error`` records the cause, and
# ``/health`` keeps reporting 200 so the process stays "up" from the
# orchestrator's perspective. ``/health/db`` is the dedicated probe that
# distinguishes "process up but DB unreachable" (503) from "DB reachable"
# (200) — uptime monitors (Better Stack etc.) point at this URL to drive
# DB-specific alerting.
#
# These tests pin that contract without spinning up a real asyncpg pool:
# the AsyncClient/ASGITransport pair used in the rest of this file does
# NOT run FastAPI lifespan events, so ``app.state.db_pool`` is whatever
# we assign before issuing the request.


class _FakeAcquireCM:
    """Minimal ``async with pool.acquire() as conn`` stand-in.

    asyncpg's real ``Pool.acquire()`` returns an async context manager
    that yields a Connection. We only need ``fetchval`` on the yielded
    object for the SELECT 1 probe — everything else is irrelevant to
    /health/db. Using a hand-rolled CM is clearer than wrestling
    AsyncMock into context-manager shape.
    """

    def __init__(self, conn: object, *, raise_on_enter: BaseException | None = None) -> None:
        self._conn = conn
        self._raise_on_enter = raise_on_enter

    async def __aenter__(self) -> object:
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_health_db_returns_503_when_pool_is_none(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pool + no DSN to retry ⇒ degraded.

    Monkeypatches get_settings so the new lazy retry in
    ``try_init_pool`` sees no DSN to try — mirroring the real
    "DATABASE_URL unset" boot path. Without this patch CI's
    DATABASE_URL would let try_init_pool actually create a pool
    against the test DB and flip this to a 200.
    """

    settings_stub = MagicMock()
    settings_stub.database_url = None
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app.state.db_pool = None
    app.state.db_pool_init_error = "DATABASE_URL not set"

    response = await client.get("/health/db")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"]["available"] is False
    assert body["db"]["init_error"] == "DATABASE_URL not set"


async def test_health_db_self_heals_when_lazy_init_succeeds(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When boot left ``db_pool=None`` but the upstream is now reachable,
    a single ``/health/db`` hit lazily creates the pool and returns 200.

    This is the self-healing path that lets an uptime monitor (or a
    manual probe) recover the running app after a transient Supabase
    pause / DNS hiccup, without requiring an operator redeploy. The
    lazy init helper updates app.state.db_pool + clears
    db_pool_init_error so every subsequent get_db sees the fresh pool.
    """

    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=1)
    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(return_value=_FakeAcquireCM(conn))

    async def fake_create_pool() -> object:
        return fake_pool

    monkeypatch.setattr("src.core.db.create_pool", fake_create_pool)
    settings_stub = MagicMock()
    settings_stub.database_url = MagicMock()  # any truthy non-None
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    # Boot left the pool dead with an upstream error captured. Reset the
    # cooldown anchor so the test isn't dependent on module-level state
    # from earlier tests.
    app.state.db_pool = None
    app.state.db_pool_init_error = "InternalServerError: tenant not found"
    app.state.db_pool_last_attempt = None

    response = await client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": {"available": True}}
    # Self-healed: state mutations visible to every subsequent get_db.
    assert app.state.db_pool is fake_pool
    assert app.state.db_pool_init_error is None


async def test_health_db_returns_200_when_pool_acquires(
    app: FastAPI, client: AsyncClient
) -> None:
    """Healthy pool ⇒ 200 + ``db.available = True``.

    The fake pool's ``acquire()`` returns an async CM yielding a fake
    connection whose ``fetchval`` resolves to ``1`` — matching what
    asyncpg returns for ``SELECT 1``.
    """

    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=1)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireCM(conn))
    app.state.db_pool = pool
    app.state.db_pool_init_error = None

    response = await client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": {"available": True}}
    conn.fetchval.assert_awaited_once_with("SELECT 1")


async def test_health_db_returns_503_on_runtime_error(
    app: FastAPI, client: AsyncClient
) -> None:
    """Pool present but ``acquire()`` blows up ⇒ degraded + runtime_error.

    Models the "DB went away at runtime" path (network flap, Supabase
    pooler restart, credentials rotated). The probe must report the
    exception type + message so on-call can grep the response without
    pulling logs.
    """

    boom = ConnectionError("upstream down")
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireCM(None, raise_on_enter=boom))
    app.state.db_pool = pool
    app.state.db_pool_init_error = None

    response = await client.get("/health/db")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"]["available"] is False
    assert "ConnectionError" in body["db"]["runtime_error"]
    assert "upstream down" in body["db"]["runtime_error"]


async def test_health_remains_200_even_when_db_pool_none(
    app: FastAPI, client: AsyncClient
) -> None:
    """The whole point of the graceful-degradation contract.

    ``/health`` is the process-liveness probe — it must NEVER return 503
    just because the DB pool failed to open. If it did, the orchestrator
    would restart the container in a crash loop and we'd lose the very
    /health/db signal we need to diagnose the DB outage.
    """

    app.state.db_pool = None
    app.state.db_pool_init_error = "DATABASE_URL not set"

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ── /health/worker — Celery fleet probe ─────────────────────────────────
#
# Every test patches ``src.tasks.celery_app.ping_workers`` (the endpoint
# does a local import, mirroring the /health/db ↔ try_init_pool pattern).
# None of them touch a real broker: in CI's redis-on job a live ping
# would block ~1s per test and return [] anyway (no worker is running),
# i.e. flaky-slow for zero coverage.


async def test_health_worker_returns_503_when_broker_unconfigured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ping_workers → None means the broker is the memory:// test stub —
    there is nothing to probe, and that is an operator-visible state."""

    monkeypatch.setattr("src.tasks.celery_app.ping_workers", lambda timeout: None)

    response = await client.get("/health/worker")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["worker"]["available"] is False
    assert body["worker"]["reason"] == "broker_not_configured"


async def test_health_worker_returns_503_when_no_workers_respond(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broker reachable but zero pongs ⇒ fleet dead/booting. This is the
    'generate will hang forever' state the probe exists to expose."""

    monkeypatch.setattr("src.tasks.celery_app.ping_workers", lambda timeout: [])

    response = await client.get("/health/worker")

    assert response.status_code == 503
    body = response.json()
    assert body["worker"]["available"] is False
    assert body["worker"]["reason"] == "no_workers_responded"


async def test_health_worker_returns_200_when_worker_replies(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.tasks.celery_app.ping_workers",
        lambda timeout: ["celery@grantwriter-worker"],
    )

    response = await client.get("/health/worker")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "worker": {
            "available": True,
            "workers": ["celery@grantwriter-worker"],
            "count": 1,
        },
    }


async def test_health_worker_returns_503_on_broker_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transport failure (KV dead → connection refused) must surface the
    exception type so on-call can tell 'KV down' from 'no worker'."""

    def _boom(timeout: float) -> list[str] | None:
        raise ConnectionError("kv down")

    monkeypatch.setattr("src.tasks.celery_app.ping_workers", _boom)

    response = await client.get("/health/worker")

    assert response.status_code == 503
    body = response.json()
    assert body["worker"]["available"] is False
    assert "ConnectionError" in body["worker"]["error"]
    assert "kv down" in body["worker"]["error"]


async def test_health_remains_200_when_worker_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decoupling contract: a dead worker fleet must never take down the
    process-liveness probe — same rule as /health vs /health/db."""

    monkeypatch.setattr("src.tasks.celery_app.ping_workers", lambda timeout: [])

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
