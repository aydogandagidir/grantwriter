"""Async Postgres pool with pgvector codecs registered.

The pool is created at app startup (see `src/main.py` lifespan) and stored on
`app.state.db_pool`. Route handlers acquire connections via `Depends(get_db)`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections.abc import AsyncIterator

import asyncpg
from fastapi import FastAPI, HTTPException, Request, status
from pgvector.asyncpg import register_vector

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# NOTE: ``Request`` MUST be imported at runtime (not under TYPE_CHECKING) —
# FastAPI inspects the parameter annotation of ``get_db`` to recognise it
# as the special ``Request`` injection. With a string-only annotation
# (``from __future__ import annotations`` + TYPE_CHECKING import), FastAPI
# falls back to treating ``request`` as a query parameter and every
# endpoint that depends on ``get_db`` 422s with
# ``loc=["query","request"], msg="Field required"``.


# Supabase's connection-string snippets — and the IPv6 "direct connection"
# template people adapt for the pooler — frequently leave the host wrapped
# in brackets, e.g. ``@[aws-1-...pooler.supabase.com]:5432``. Python 3.11+
# treats ``[...]`` as an IPv6 literal in ``urlsplit`` and raises ValueError
# when the inner text isn't a real IP, crashing asyncpg before it can even
# connect (Application startup failed → boot loop). We strip those
# accidental brackets while leaving genuine IPv6 literals — which legally
# require brackets — untouched.
_BRACKETED_HOST_RE = re.compile(r"(?P<pre>@|://)\[(?P<host>[^\[\]]+)\](?=[:/?#]|$)")


def _normalize_dsn(dsn: str) -> str:
    """Strip erroneous ``[...]`` brackets around a non-IP host in a DSN."""

    def _unwrap(match: re.Match[str]) -> str:
        host = match.group("host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Not an IP → the brackets are a copy-paste artifact; drop them.
            return f"{match.group('pre')}{host}"
        # Genuine IPv6 literal → brackets are required; leave untouched.
        return match.group(0)

    return _BRACKETED_HOST_RE.sub(_unwrap, dsn.strip())


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register pgvector codecs on each new pooled connection.

    `register_vector` from pgvector.asyncpg installs both `vector` and `halfvec`
    type adapters in recent versions (>=0.3.0). On older releases only `vector`
    is registered; the script and scorer will still work because they cast to
    halfvec at the SQL boundary.
    """
    await register_vector(conn)


async def create_pool() -> asyncpg.Pool:
    """Open the application's asyncpg pool.

    Raises ``RuntimeError`` if ``DATABASE_URL`` is not configured — the
    application's lifespan should check for that before calling.
    """
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")
    dsn = _normalize_dsn(settings.database_url.get_secret_value())
    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=_init_connection,
        # Supabase's pooler (Supavisor/pgbouncer) runs in transaction mode
        # on port 6543, which does not support server-side *named* prepared
        # statements. asyncpg caches prepared statements by default, so a
        # transaction-pooler DSN would fail mid-request. Disabling the
        # statement cache keeps us compatible with the "connection-pooling
        # URL" that .env.production.example tells operators to use, in
        # either transaction (6543) or session (5432) mode. The cost is a
        # re-prepare per query — negligible at our scale, and a no-op cost
        # on direct/session connections.
        statement_cache_size=0,
    )
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    return pool


# ── Lazy pool retry (self-healing on transient upstream failure) ──────
#
# When create_pool() fails at lifespan startup (Supabase paused, DNS
# hiccup, transient auth error), the resilient lifespan sets
# ``app.state.db_pool = None`` and the container stays up so /health
# can still report. Without this helper the running app NEVER retries —
# it stays "DB unavailable" until the operator triggers a redeploy,
# even if the upstream came back five minutes later.
#
# ``try_init_pool`` is called from /health/db: when an uptime monitor
# (or a manual probe) hits the endpoint, we attempt one create_pool
# call. On success the pool is wired into app.state for every
# subsequent ``get_db`` to use; the live container self-heals without
# a redeploy. A cooldown caps the load on a still-broken upstream and
# an asyncio.Lock serializes concurrent retries so we never spawn two
# create_pool tasks racing for the same state.

_LAZY_INIT_LOCK: asyncio.Lock | None = None
_LAZY_RETRY_MIN_INTERVAL_S = 30.0


def _get_lazy_init_lock() -> asyncio.Lock:
    """Build the module-level lock lazily.

    Constructing ``asyncio.Lock`` at import time binds it to whatever
    loop happens to be current then — under pytest-asyncio that's
    often a stale loop and the lock raises ``RuntimeError: ... is
    bound to a different event loop`` later. Build-on-first-use ties
    it to the running loop.
    """

    global _LAZY_INIT_LOCK  # noqa: PLW0603 — deliberate singleton lazy-init; binding the lock to whichever loop is current on first use avoids "Lock bound to a different event loop" under pytest-asyncio
    if _LAZY_INIT_LOCK is None:
        _LAZY_INIT_LOCK = asyncio.Lock()
    return _LAZY_INIT_LOCK


async def try_init_pool(app: FastAPI) -> asyncpg.Pool | None:
    """Return the app's asyncpg pool, lazily attempting (re-)init if None.

    Returns the (newly or already) initialised pool, or ``None`` on
    failure (with ``app.state.db_pool_init_error`` populated). Callers
    decide whether to surface the failure or carry on.

    Cooldown: requests within ``_LAZY_RETRY_MIN_INTERVAL_S`` of the
    last attempt get ``None`` back immediately without a retry. Stops
    a steady stream of uptime-monitor probes from hammering a still-
    broken upstream.

    Concurrency: a module-level ``asyncio.Lock`` ensures at most one
    create_pool task is in flight; the rest wait for it and read the
    final ``app.state.db_pool``.
    """

    pool = getattr(app.state, "db_pool", None)
    if pool is not None:
        return pool

    settings = get_settings()
    if settings.database_url is None:
        # Nothing to retry — the example/secret matrix is the fix.
        return None

    now = time.monotonic()
    last = getattr(app.state, "db_pool_last_attempt", None)
    if last is not None and now - last < _LAZY_RETRY_MIN_INTERVAL_S:
        return None

    async with _get_lazy_init_lock():
        # Re-check inside the lock — another coroutine may have just
        # succeeded.
        pool = getattr(app.state, "db_pool", None)
        if pool is not None:
            return pool

        app.state.db_pool_last_attempt = time.monotonic()
        try:
            pool = await create_pool()
        except Exception as exc:
            # Same catch-all rationale as the lifespan: every upstream
            # failure mode (asyncpg.exceptions.*, OSError, DNS, auth, …)
            # is operator-fixable and must not crash the running app.
            app.state.db_pool_init_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "db_pool_lazy_init_failed",
                extra={
                    "event": "db_pool_lazy_init_failed",
                    "exc_type": type(exc).__name__,
                    "exc_repr": repr(exc),
                },
                exc_info=exc,
            )
            return None

        app.state.db_pool = pool
        app.state.db_pool_init_error = None
        logger.info("db_pool_lazily_initialised")
        return pool


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency yielding a connection from the app pool.

    Returns 503 if the pool is not initialised — this happens when the app
    starts without ``DATABASE_URL`` set (test mode, smoke deploys).
    """
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )
    async with pool.acquire() as conn:
        yield conn
