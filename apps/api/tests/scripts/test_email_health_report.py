"""Smoke tests for the email-health CLI's arg parsing + exit codes.

Heavy lifting is exercised by ``tests/notifications/test_health.py``;
here we just verify the CLI maps the report onto the right exit code
(0 = no alert, 1 = alert) and rejects bad args.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from src.notifications.health import HealthReport

from scripts.email_health_report import _amain, _build_parser


def _args(**overrides: object) -> argparse.Namespace:
    base = {
        "database_url": "postgresql://test/test",
        "window_minutes": 60,
        "hard_bounce_threshold": 0.02,
        "complaint_threshold": 0.001,
        "min_sample": 50,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_parser_defaults_window_and_thresholds() -> None:
    parser = _build_parser()
    ns = parser.parse_args(["--database-url", "postgresql://x"])
    assert ns.window_minutes == 60
    assert ns.hard_bounce_threshold == 0.02
    assert ns.complaint_threshold == 0.001
    assert ns.min_sample == 50


def test_amain_rejects_missing_database_url(capsys: pytest.CaptureFixture[str]) -> None:
    rc = asyncio.run(_amain(_args(database_url=None)))
    assert rc == 2
    assert "database-url" in capsys.readouterr().err


def test_amain_rejects_non_positive_window(capsys: pytest.CaptureFixture[str]) -> None:
    rc = asyncio.run(_amain(_args(window_minutes=0)))
    assert rc == 2
    assert "window-minutes" in capsys.readouterr().err


def test_amain_rejects_out_of_range_threshold(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = asyncio.run(_amain(_args(hard_bounce_threshold=1.5)))
    assert rc == 2
    assert "hard-bounce-threshold" in capsys.readouterr().err


def _stub_report(*, alerts: list[str] | None = None) -> HealthReport:
    return HealthReport(
        window_seconds=3600,
        total_events=10,
        delivered=8,
        hard_bounces=1,
        soft_bounces=1,
        complaints=0,
        other=0,
        hard_bounce_rate=0.1,
        complaint_rate=0.0,
        sample_size=10,
        alerts=alerts or [],
    )


def test_amain_returns_zero_when_no_alerts_fire(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_pool = AsyncMock()
    fake_pool.close = AsyncMock()
    # Acquire returns an async context manager — emulate it.
    fake_acquire = AsyncMock()
    fake_acquire.__aenter__ = AsyncMock(return_value=object())
    fake_acquire.__aexit__ = AsyncMock(return_value=None)
    fake_pool.acquire = lambda: fake_acquire

    with (
        patch(
            "scripts.email_health_report.asyncpg.create_pool",
            new=AsyncMock(return_value=fake_pool),
        ),
        patch(
            "scripts.email_health_report.evaluate_health",
            new=AsyncMock(return_value=_stub_report(alerts=[])),
        ),
    ):
        rc = asyncio.run(_amain(_args()))

    assert rc == 0
    blob = capsys.readouterr().out.strip()
    parsed = json.loads(blob)
    assert parsed["delivered"] == 8
    assert parsed["alerts"] == []


def test_amain_returns_one_when_alert_fires(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_pool = AsyncMock()
    fake_pool.close = AsyncMock()
    fake_acquire = AsyncMock()
    fake_acquire.__aenter__ = AsyncMock(return_value=object())
    fake_acquire.__aexit__ = AsyncMock(return_value=None)
    fake_pool.acquire = lambda: fake_acquire

    with (
        patch(
            "scripts.email_health_report.asyncpg.create_pool",
            new=AsyncMock(return_value=fake_pool),
        ),
        patch(
            "scripts.email_health_report.evaluate_health",
            new=AsyncMock(
                return_value=_stub_report(
                    alerts=["hard_bounce_rate 0.10 exceeds threshold 0.02"]
                )
            ),
        ),
    ):
        rc = asyncio.run(_amain(_args()))

    assert rc == 1
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["alerts"] == ["hard_bounce_rate 0.10 exceeds threshold 0.02"]
