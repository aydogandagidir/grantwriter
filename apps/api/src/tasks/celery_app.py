"""Celery application factory.

The single Celery app is shared by every task module. Broker / result
backend default to :setting:`REDIS_URL` when the dedicated Celery
settings are unset — keeps local dev simple and CI fast.

Worker entrypoint (per :file:`Makefile`):

    poetry run celery -A src.tasks.celery_app worker --loglevel=info
"""

from __future__ import annotations

from celery import Celery

from src.core.config import get_settings

_settings = get_settings()


def _broker_url() -> str:
    if _settings.celery_broker_url:
        return _settings.celery_broker_url
    if _settings.redis_url:
        return _settings.redis_url.get_secret_value()
    # In tests the broker is never reached (we drive task functions
    # directly), but Celery refuses to construct without one — use a
    # safe in-memory transport.
    return "memory://"


def _result_backend() -> str:
    if _settings.celery_result_backend:
        return _settings.celery_result_backend
    if _settings.redis_url:
        return _settings.redis_url.get_secret_value()
    return "cache+memory://"


celery_app: Celery = Celery(
    "bluedev_grantwriter",
    broker=_broker_url(),
    backend=_result_backend(),
    include=[
        "src.tasks.exports",
        "src.tasks.guidelines",
        "src.tasks.orchestrator",
        "src.tasks.scrapers",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # Without this, states jump PENDING→SUCCESS and /jobs/{id} reports
    # "queued" for the entire 5-15 min saga — operators can't tell a
    # running generation from a dead worker. STARTED maps to "running".
    task_track_started=True,
    # Celery ≥5.3 deprecation default-flip guard: keep retrying the broker
    # at worker boot instead of dying if Key Value is briefly unavailable
    # (e.g., both services restart after a region incident).
    broker_connection_retry_on_startup=True,
)


def ping_workers(timeout: float = 1.0) -> list[str] | None:
    """Broadcast a ping to the worker fleet; return sorted hostnames.

    Return contract (consumed by ``/health/worker`` in ``src.main``):

    * ``None``  — broker unconfigured (in-memory test stub). A broadcast
      on the memory transport has no consumers and would just block for
      the full ``timeout``, so we short-circuit BEFORE inspect.
    * ``[]``    — broker reachable but no worker replied within
      ``timeout`` (fleet dead or still booting). NB: with a live broker
      and zero workers, ``ping()`` blocks the full timeout then returns
      ``None`` — it does not raise; only transport failures raise.
    * ``[...]`` — pong'ing worker hostnames, sorted for stable output.

    Each call opens a fresh broker connection — fine at probe frequency
    (uptime monitor / readiness script), too heavy for any hot path.
    """

    if str(celery_app.conf.broker_url or "").startswith("memory://"):
        return None
    replies = celery_app.control.inspect(timeout=timeout).ping()
    return sorted(replies or {})


# ── Beat schedule ────────────────────────────────────────────────────────
#
# Frequencies per docs/programs/README.md. All times are UTC; the funder
# pages are unlikely to update before mid-morning Brussels / Istanbul.
# Use ``crontab`` for human-readable cron expressions; the import here is
# lazy so unit tests that don't touch beat don't pay the cost.

from celery.schedules import crontab  # noqa: E402  (kept near beat config)

celery_app.conf.beat_schedule = {
    "scrape-eu-ft-portal-daily": {
        "task": "src.tasks.scrapers.run_scraper_task",
        "schedule": crontab(hour=2, minute=0),  # 03:00 Europe/Istanbul
        "args": ("eu_ft_portal",),
        "options": {"queue": "default"},
    },
    "scrape-nlnet-weekly": {
        "task": "src.tasks.scrapers.run_scraper_task",
        "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
        "args": ("nlnet",),
        "options": {"queue": "default"},
    },
    "scrape-tubitak-weekly": {
        "task": "src.tasks.scrapers.run_scraper_task",
        "schedule": crontab(hour=4, minute=0, day_of_week="wednesday"),
        "args": ("tubitak",),
        "options": {"queue": "default"},
    },
}


__all__ = ["celery_app", "ping_workers"]
