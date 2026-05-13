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
)


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


__all__ = ["celery_app"]
