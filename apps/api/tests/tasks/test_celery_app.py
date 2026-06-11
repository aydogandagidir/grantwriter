"""Unit tests for the Celery app config + the ``ping_workers`` helper.

``ping_workers`` is the broker-side primitive behind ``/health/worker``
(endpoint tests live in ``tests/test_health.py``). These tests pin:

* the memory-broker short-circuit (CI job 1 exports a real REDIS_URL,
  so the ambient broker is NOT memory there — every case patches
  ``celery_app.conf.broker_url`` / ``celery_app.control`` explicitly
  rather than trusting the environment),
* pong sorting (stable output for monitors and humans),
* the conf contract the worker topology depends on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.tasks.celery_app import celery_app, ping_workers


def test_ping_workers_returns_none_on_memory_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory:// has no consumers — a broadcast would block for the full
    timeout for nothing. The helper must short-circuit BEFORE inspect;
    we prove that by making inspect explode if touched."""

    monkeypatch.setattr(celery_app.conf, "broker_url", "memory://")
    inspect_trap = MagicMock(side_effect=AssertionError("inspect must not be called"))
    monkeypatch.setattr(celery_app.control, "inspect", inspect_trap)

    assert ping_workers(timeout=0.1) is None
    inspect_trap.assert_not_called()


def test_ping_workers_sorts_pong_hostnames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reply-dict ordering is transport-dependent; the helper sorts so
    monitor output and test assertions are stable."""

    monkeypatch.setattr(celery_app.conf, "broker_url", "redis://stub:6379/0")
    inspector = MagicMock()
    inspector.ping.return_value = {
        "celery@worker-b": {"ok": "pong"},
        "celery@worker-a": {"ok": "pong"},
    }
    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout: inspector)

    assert ping_workers(timeout=0.1) == ["celery@worker-a", "celery@worker-b"]


def test_ping_workers_returns_empty_list_when_no_pong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live broker + zero workers: ``ping()`` blocks the full timeout and
    returns ``None`` (it does NOT raise). The helper folds that into
    ``[]`` — 'broker up, fleet silent' — distinct from the ``None``
    'broker unconfigured' state."""

    monkeypatch.setattr(celery_app.conf, "broker_url", "redis://stub:6379/0")
    inspector = MagicMock()
    inspector.ping.return_value = None
    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout: inspector)

    assert ping_workers(timeout=0.1) == []


def test_celery_conf_contract() -> None:
    """The worker topology (render.yaml flags, /jobs mapping, runbook
    §5w semantics) assumes exactly this conf. A drive-by conf change
    must consciously update all three, so pin it here.

    * task_track_started — /jobs reports "running" mid-saga instead of
      a 15-minute "queued" lie.
    * task_acks_late + prefetch 1 — a SIGTERM'd saga re-delivers after
      visibility_timeout instead of being lost.
    * broker_connection_retry_on_startup — worker boot survives a brief
      KV blip instead of dying.
    """

    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_connection_retry_on_startup is True
