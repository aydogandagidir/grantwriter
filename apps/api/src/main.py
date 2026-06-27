"""FastAPI application entrypoint.

Use the ``create_app`` factory for tests and overrides; the module-level
``app`` exists so production runs as ``uvicorn src.main:app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes.audit import router as audit_router
from src.api.routes.billing import router as billing_router
from src.api.routes.calls import router as calls_router
from src.api.routes.citations import router as citations_router
from src.api.routes.comments import (
    comment_router as comments_id_router,
)
from src.api.routes.comments import (
    proposal_router as comments_proposal_router,
)
from src.api.routes.ideas import router as ideas_router
from src.api.routes.invitations import (
    public_router as invitations_public_router,
)
from src.api.routes.invitations import (
    tenant_router as invitations_tenant_router,
)
from src.api.routes.jobs import router as jobs_router
from src.api.routes.llm_config import router as llm_config_router
from src.api.routes.me import router as me_router
from src.api.routes.members import router as members_router
from src.api.routes.notifications import router as notifications_router
from src.api.routes.onboarding import router as onboarding_router
from src.api.routes.organizations import router as organizations_router
from src.api.routes.programmes import router as programmes_router
from src.api.routes.proposals import router as proposals_router
from src.api.routes.provenance import router as provenance_router
from src.api.routes.usage import router as usage_router
from src.api.routes.versions import router as versions_router
from src.core.config import SettingsDep, get_settings
from src.core.db import create_pool
from src.core.logging import configure_logging
from src.core.observability import init_observability
from src.core.preflight import run_preflight

_PACKAGE_NAME = "bluedev-grantwriter-api"

logger = logging.getLogger(__name__)


def _resolve_version(fallback: str) -> str:
    """Return the installed package version, falling back to settings.

    ``importlib.metadata.version`` is the source of truth in production
    (no drift from ``pyproject.toml``). In source-mode tests the package
    may not be installed yet, so we degrade to the configured fallback.

    Defensive against the ``""`` case: a stale editable install whose
    ``METADATA`` file was written before ``pyproject.toml`` had its
    version set can return an empty string instead of raising. We treat
    that the same as PackageNotFoundError so FastAPI's
    ``assert self.version`` assertion (OpenAPI requires a non-empty
    version string) never fires.
    """

    try:
        installed = version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return fallback or "0.1.0"
    return installed or fallback or "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the asyncpg pool when ``DATABASE_URL`` is configured.

    DELIBERATELY RESILIENT: a pool init failure sets ``app.state.db_pool``
    to ``None`` (matching the existing "DATABASE_URL unset" path) and logs
    LOUDLY, but does NOT crash boot. The app stays up, ``/health``
    responds 200, DB-bound routes return 503 via ``get_db``, and
    ``/health/db`` reports the underlying error so uptime monitors /
    Better Stack can distinguish "process up but DB unreachable" from
    "process down".
    """

    settings = get_settings()
    # Production-only preflight: validates every REQUIRED env var is set,
    # not a `<placeholder>`, and (for DATABASE_URL) not a bracketed
    # non-IPv6 host. Runs BEFORE observability/db init so a misconfig
    # exits the container with a one-line message instead of a deep
    # asyncpg/jwt traceback halfway through startup. No-op in dev/CI
    # because APP_ENV defaults to "development" there.
    if settings.app_env == "production":
        run_preflight()
    # Stand up Sentry + Logtail before opening any other resource so a
    # failure during pool init still ships an error to Sentry.
    app.state.observability_report = init_observability(settings)
    app.state.db_pool_init_error = None
    if settings.database_url is None:
        app.state.db_pool = None
        app.state.db_pool_init_error = "DATABASE_URL not set"
        logger.warning("db_pool_skipped: DATABASE_URL not set")
    else:
        try:
            app.state.db_pool = await create_pool()
            logger.info("db_pool_opened")
        # Catching bare Exception is intentional: every form of pool-init
        # failure (asyncpg.exceptions.*, asyncio.TimeoutError, OSError, DNS,
        # auth, …) is upstream and operator-fixable. Keep the app up so
        # /health stays 200 and /health/db reports the cause; never let an
        # upstream DB issue boot-loop the container.
        except Exception as exc:
            app.state.db_pool = None
            app.state.db_pool_init_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "db_pool_init_failed",
                extra={
                    "event": "db_pool_init_failed",
                    "exc_type": type(exc).__name__,
                    "exc_repr": repr(exc),
                },
                exc_info=exc,
            )
    try:
        yield
    finally:
        if app.state.db_pool is not None:
            await app.state.db_pool.close()
            logger.info("db_pool_closed")
        # Redis client is opened lazily on first SSE request — close it
        # here if any handler created one.
        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is not None:
            await redis_client.aclose()
            logger.info("redis_client_closed")


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""

    settings = get_settings()
    configure_logging(settings.log_level)

    app_version = _resolve_version(settings.app_version)

    app = FastAPI(
        title="Bluedev GrantWriter API",
        version=app_version,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={
                "event": "unhandled_exception",
                "path": request.url.path,
                "method": request.method,
                "exc_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.get("/health", tags=["meta"])
    async def health(settings: SettingsDep) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": _resolve_version(settings.app_version),
        }

    @app.get("/health/db", tags=["meta"])
    async def health_db(request: Request) -> JSONResponse:
        """DB-aware health probe — Better Stack / uptime monitors hit this to know
        whether the app can actually serve DB-bound traffic, distinct from
        /health which only verifies the process is up.

        Self-healing: when the boot-time pool is ``None`` (Supabase paused,
        DNS hiccup at startup, transient auth error), each hit attempts a
        lazy re-init via ``try_init_pool``. A 30s cooldown caps load on a
        still-broken upstream and an asyncio.Lock serializes concurrent
        retries. When the upstream comes back, the next probe (or a real
        request) sees the new pool — no operator redeploy required.
        """

        # Local import: try_init_pool depends on FastAPI types and is only
        # touched by this one endpoint, so keep the symbol out of the
        # module-level import graph that route handlers spin up.
        from src.core.db import try_init_pool

        pool = await try_init_pool(request.app)
        if pool is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "db": {
                        "available": False,
                        "init_error": getattr(request.app.state, "db_pool_init_error", "unknown"),
                    },
                },
            )
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        # Catch-all: any acquire/query failure (pool exhausted, upstream
        # dropped, auth rotated) becomes a degraded-but-reachable 503 so
        # the uptime monitor sees "DB unhappy" instead of "endpoint gone".
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "db": {"available": False, "runtime_error": f"{type(exc).__name__}: {exc}"},
                },
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "db": {"available": True}},
        )

    # Deliberately a sync `def` (every other route is async): the broker
    # broadcast in ping_workers() BLOCKS up to its timeout. Starlette runs
    # sync routes on the threadpool, so the event loop never stalls while
    # we wait for worker pongs.
    @app.get("/health/worker", tags=["meta"])
    def health_worker() -> JSONResponse:
        """Worker-fleet health probe — distinguishes "API up" from "the
        Celery fleet that runs generate/export is actually consuming".

        Reads the broker through ``ping_workers``:
        - broker unconfigured (memory:// stub) → 503 ``broker_not_configured``
        - broker up, no pong within timeout    → 503 ``no_workers_responded``
        - transport failure (KV down/refused)  → 503 with the typed error
        - ≥1 pong → 200 with the sorted hostname list

        ``/health`` never 503s for worker reasons — same decoupling
        contract as ``/health/db``.
        """

        # Local import — mirrors the /health/db ↔ try_init_pool pattern so
        # tests monkeypatch ``src.tasks.celery_app.ping_workers`` at the
        # source module instead of fighting an early-bound reference here.
        from src.tasks.celery_app import ping_workers

        try:
            workers = ping_workers(timeout=1.0)
        # Catch-all: kombu raises transport-specific errors (ConnectionError,
        # OSError subclasses, redis exceptions) when the broker is dead.
        # All of them mean the same thing to a monitor: fleet unreachable.
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "worker": {
                        "available": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                },
            )

        if workers is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "worker": {"available": False, "reason": "broker_not_configured"},
                },
            )
        if not workers:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "worker": {"available": False, "reason": "no_workers_responded"},
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "worker": {"available": True, "workers": workers, "count": len(workers)},
            },
        )

    @app.get("/health/sentry-test", tags=["meta"])
    async def health_sentry_test(settings: SettingsDep) -> dict[str, Any]:
        """Deliberate-exception probe for the Sentry pipeline (TICKET-003).

        Returns 503 when Sentry isn't configured — keeps unit tests + dev
        laptops quiet. When Sentry is wired, raises a tagged exception so
        the operator can confirm:

        - The event lands in the Sentry project.
        - The PII scrubber redacts the BYOK key shape we deliberately
          embed in the message.
        - The release tag matches the deployed git SHA.
        """

        if settings.sentry_dsn is None:
            return {"status": "skipped", "reason": "SENTRY_DSN not configured"}

        # The canary string includes a BYOK-key shape so the operator can
        # verify scrub_event() redacted it before the event left the box.
        canary = "sk-ant-AAAAAAAAAAAAAAAAAAAAAA"
        raise RuntimeError(
            f"sentry smoke test triggered (release={settings.sentry_release or '<unset>'}, "
            f"canary={canary})"
        )

    app.include_router(proposals_router)
    app.include_router(citations_router)
    app.include_router(jobs_router)
    app.include_router(llm_config_router)
    app.include_router(usage_router)
    app.include_router(audit_router)
    app.include_router(billing_router)
    app.include_router(me_router)
    app.include_router(invitations_tenant_router)
    app.include_router(invitations_public_router)
    app.include_router(members_router)
    app.include_router(versions_router)
    app.include_router(comments_proposal_router)
    app.include_router(comments_id_router)
    app.include_router(onboarding_router)
    app.include_router(provenance_router)
    app.include_router(notifications_router)
    app.include_router(programmes_router)
    app.include_router(calls_router)
    app.include_router(ideas_router)
    app.include_router(organizations_router)

    return app


app = create_app()
