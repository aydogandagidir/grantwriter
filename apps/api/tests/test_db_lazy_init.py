"""Unit tests for ``try_init_pool`` — the lazy retry helper that lets a
running app self-heal when the boot-time pool init failed (Supabase
paused, DNS hiccup, transient auth error).

These exercise the helper directly with a fake app object so we don't
need a TestClient or a live Postgres. The endpoint-level integration
tests live in ``test_health.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from src.core.db import _LAZY_RETRY_MIN_INTERVAL_S, try_init_pool


@pytest.fixture(autouse=True)
def _reset_lazy_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level ``_LAZY_INIT_LOCK`` to ``None`` before each
    test so ``_get_lazy_init_lock()`` rebuilds it on the current event
    loop — without this, a lock created on a previous test's loop
    raises ``RuntimeError: ... bound to a different event loop``.
    """

    monkeypatch.setattr("src.core.db._LAZY_INIT_LOCK", None)


def _fake_app() -> Any:
    """Build a minimal FastAPI-shaped app stub with a fresh ``state``
    namespace per test. Uses ``SimpleNamespace`` (not ``MagicMock``)
    because we need genuine ``getattr(..., default)`` semantics for
    the optional ``db_pool_last_attempt`` attribute."""

    app = SimpleNamespace()
    app.state = SimpleNamespace()
    app.state.db_pool = None
    app.state.db_pool_init_error = None
    return app


async def test_returns_existing_pool_without_calling_create() -> None:
    """If a pool is already cached on ``app.state``, return it as-is —
    we MUST not pay the cost (or risk) of another create_pool round
    trip on the hot path."""

    app = _fake_app()
    existing = MagicMock(name="already-open-pool")
    app.state.db_pool = existing
    result = await try_init_pool(app)
    assert result is existing


async def test_returns_none_when_dsn_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to retry when DATABASE_URL was never wired — the
    operator-side fix is the secret matrix, not a lazy reconnect."""

    settings_stub = MagicMock()
    settings_stub.database_url = None
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app = _fake_app()
    result = await try_init_pool(app)
    assert result is None
    # We MUST NOT mutate an existing init_error here — the lifespan
    # already set a more accurate one ("DATABASE_URL not set").
    assert app.state.db_pool_init_error is None


async def test_lazy_init_succeeds_and_wires_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self-healing happy path: pool was ``None`` because the
    upstream was down at boot; ``try_init_pool`` retries create_pool,
    caches the new pool on state, and CLEARS the stale init_error so
    subsequent ``get_db`` calls succeed."""

    fake_pool = MagicMock(name="freshly-opened-pool")

    async def fake_create() -> object:
        return fake_pool

    monkeypatch.setattr("src.core.db.create_pool", fake_create)
    settings_stub = MagicMock()
    settings_stub.database_url = MagicMock()
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app = _fake_app()
    app.state.db_pool_init_error = "InternalServerError: tenant not found"

    result = await try_init_pool(app)

    assert result is fake_pool
    assert app.state.db_pool is fake_pool
    assert app.state.db_pool_init_error is None  # cleared on recovery


async def test_lazy_init_failure_records_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``create_pool`` raises, we MUST record the error on state so
    the next ``/health/db`` probe surfaces it AND return ``None`` so
    callers 503 the current request. The recorded message must include
    BOTH the exception type and the message — operators rely on the
    type to disambiguate similar-looking failures."""

    async def fake_create_fails() -> object:
        raise ConnectionError("upstream down")

    monkeypatch.setattr("src.core.db.create_pool", fake_create_fails)
    settings_stub = MagicMock()
    settings_stub.database_url = MagicMock()
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app = _fake_app()
    result = await try_init_pool(app)

    assert result is None
    assert app.state.db_pool is None
    assert "ConnectionError" in app.state.db_pool_init_error
    assert "upstream down" in app.state.db_pool_init_error


async def test_cooldown_blocks_repeated_attempts_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call within ``_LAZY_RETRY_MIN_INTERVAL_S`` MUST NOT
    trigger ``create_pool`` again — protects a still-broken upstream
    from a stream of uptime-monitor probes hammering it back into
    submission. The pool stays ``None``; the cached error is what the
    caller surfaces."""

    call_count = 0

    async def fake_create_keeps_failing() -> object:
        nonlocal call_count
        call_count += 1
        raise ConnectionError("still down")

    monkeypatch.setattr("src.core.db.create_pool", fake_create_keeps_failing)
    settings_stub = MagicMock()
    settings_stub.database_url = MagicMock()
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app = _fake_app()

    # First call → actually tries create_pool, fails, records state.
    first = await try_init_pool(app)
    assert first is None
    assert call_count == 1

    # Second call within cooldown → early-return None, no extra retry.
    second = await try_init_pool(app)
    assert second is None
    assert call_count == 1  # MUST not have incremented


async def test_retry_allowed_after_cooldown_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside the cooldown window the helper retries again — necessary
    for self-healing once the upstream actually recovers."""

    call_count = 0

    async def fake_create() -> object:
        nonlocal call_count
        call_count += 1
        raise ConnectionError("still down")

    monkeypatch.setattr("src.core.db.create_pool", fake_create)
    settings_stub = MagicMock()
    settings_stub.database_url = MagicMock()
    monkeypatch.setattr("src.core.db.get_settings", lambda: settings_stub)

    app = _fake_app()

    await try_init_pool(app)
    assert call_count == 1

    # Backdate the last-attempt timestamp past the cooldown window.
    app.state.db_pool_last_attempt -= _LAZY_RETRY_MIN_INTERVAL_S + 1

    await try_init_pool(app)
    assert call_count == 2
