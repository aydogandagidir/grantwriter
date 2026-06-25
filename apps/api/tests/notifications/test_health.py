"""Tests for the email-health analyzer + DB-bound evaluator.

Two layers:

A. **Pure ``analyze``** — fed crafted event lists, asserts counts +
   rate math + threshold trip behaviour.

B. **DB-bound ``evaluate_health``** — seeds real ``email_events`` rows
   and verifies the SQL window selector + payload→bounce_type extractor
   land on the same report the pure analyzer produces.

DB-bound tests skip when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from src.notifications.health import (
    EmailEventLite,
    HealthThresholds,
    analyze,
    evaluate_health,
)


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Pure analyzer ──────────────────────────────────────────────────────


def test_analyze_returns_zero_rates_for_empty_input() -> None:
    report = analyze([])
    assert report.total_events == 0
    assert report.sample_size == 0
    assert report.hard_bounce_rate == 0.0
    assert report.complaint_rate == 0.0
    assert report.alerts == []


def test_analyze_separates_hard_and_soft_bounces() -> None:
    events = [
        EmailEventLite(event_type="email.delivered", bounce_type=None),
        EmailEventLite(event_type="email.bounced", bounce_type="hard"),
        EmailEventLite(event_type="email.bounced", bounce_type="soft"),
        EmailEventLite(event_type="email.complained", bounce_type=None),
    ]
    report = analyze(events)
    assert report.delivered == 1
    assert report.hard_bounces == 1
    assert report.soft_bounces == 1
    assert report.complaints == 1
    assert report.sample_size == 4


def test_analyze_excludes_opens_and_clicks_from_sample_size() -> None:
    """Opens / clicks shouldn't dilute the bounce-rate denominator —
    they're not deliveries, they're post-delivery interactions."""

    events = [
        EmailEventLite(event_type="email.delivered", bounce_type=None),
        EmailEventLite(event_type="email.opened", bounce_type=None),
        EmailEventLite(event_type="email.clicked", bounce_type=None),
        EmailEventLite(event_type="email.bounced", bounce_type="hard"),
    ]
    report = analyze(events)
    assert report.sample_size == 2  # delivered + hard bounce
    assert report.other == 2  # open + click
    assert report.hard_bounce_rate == 0.5


def test_analyze_trips_hard_bounce_threshold_above_minimum_sample() -> None:
    events = [
        *[
            EmailEventLite(event_type="email.delivered", bounce_type=None)
            for _ in range(95)
        ],
        *[
            EmailEventLite(event_type="email.bounced", bounce_type="hard")
            for _ in range(5)
        ],
    ]
    report = analyze(
        events, thresholds=HealthThresholds(hard_bounce=0.02, min_sample=50)
    )
    assert report.sample_size == 100
    assert report.hard_bounce_rate == 0.05
    assert any("hard_bounce_rate" in a for a in report.alerts)


def test_analyze_does_not_alert_below_min_sample_even_at_100_pct() -> None:
    """A 100% hard bounce on a 3-event sample is noise, not signal.
    The min_sample guard mutes the alert until we have enough events
    for a meaningful rate."""

    events = [
        EmailEventLite(event_type="email.bounced", bounce_type="hard")
        for _ in range(3)
    ]
    report = analyze(events, thresholds=HealthThresholds(min_sample=50))
    assert report.hard_bounce_rate == 1.0
    assert report.alerts == []


def test_analyze_trips_complaint_threshold_independently() -> None:
    events = [
        *[
            EmailEventLite(event_type="email.delivered", bounce_type=None)
            for _ in range(99)
        ],
        EmailEventLite(event_type="email.complained", bounce_type=None),
    ]
    report = analyze(
        events,
        thresholds=HealthThresholds(complaint=0.001, min_sample=50),
    )
    assert report.complaint_rate == 0.01
    assert any("complaint_rate" in a for a in report.alerts)


def test_report_to_dict_is_json_safe() -> None:
    """The CLI prints the report via json.dumps — this is the smoke
    test that nothing in the dataclass needs custom encoding."""

    events = [EmailEventLite(event_type="email.delivered", bounce_type=None)]
    report = analyze(events)
    blob = json.dumps(report.to_dict())
    parsed = json.loads(blob)
    assert parsed["delivered"] == 1
    assert "alerts" in parsed


# ── DB-bound integration ───────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound health tests"
        )
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _seed(
    conn: asyncpg.Connection,
    *,
    event_type: str,
    bounce_type: str | None,
    received_at: datetime | None = None,
) -> str:
    event_id = f"evt-health-{uuid.uuid4()}"
    payload = {"type": event_type, "data": {}}
    if bounce_type is not None:
        payload["data"]["bounce"] = {"type": bounce_type}  # type: ignore[index]
    await conn.execute(
        """
        insert into email_events (
          provider, provider_event_id, event_type,
          recipient, payload, received_at
        ) values (
          'resend', $1, $2, $3, $4::jsonb, coalesce($5, now())
        )
        """,
        event_id,
        event_type,
        f"recipient-{uuid.uuid4()}@example.com",
        json.dumps(payload),
        received_at,
    )
    return event_id


async def _cleanup(pool: asyncpg.Pool, ids: list[str]) -> None:
    if not ids:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from email_events where provider_event_id = any($1::text[])",
            ids,
        )


async def test_evaluate_health_pulls_payload_bounce_type_correctly(
    pool: asyncpg.Pool,
) -> None:
    ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            ids.append(
                await _seed(conn, event_type="email.delivered", bounce_type=None)
            )
            ids.append(
                await _seed(conn, event_type="email.bounced", bounce_type="hard")
            )
            ids.append(
                await _seed(conn, event_type="email.bounced", bounce_type="soft")
            )
            report = await evaluate_health(
                conn,
                window=timedelta(minutes=5),
                thresholds=HealthThresholds(min_sample=1),
            )

        # Only the rows we seeded within the window should appear (other
        # parallel tests may seed rows too, so we assert >= rather than ==).
        assert report.delivered >= 1
        assert report.hard_bounces >= 1
        assert report.soft_bounces >= 1
    finally:
        await _cleanup(pool, ids)


async def test_evaluate_health_excludes_events_outside_window(
    pool: asyncpg.Pool,
) -> None:
    ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            # Seed a row 2 hours old + a fresh row — only the fresh one
            # should land inside a 5-minute window.
            stale = await _seed(
                conn,
                event_type="email.bounced",
                bounce_type="hard",
                received_at=datetime.now(UTC) - timedelta(hours=2),
            )
            fresh = await _seed(
                conn, event_type="email.delivered", bounce_type=None
            )
            ids.extend([stale, fresh])

            report = await evaluate_health(
                conn,
                window=timedelta(minutes=5),
                thresholds=HealthThresholds(min_sample=1),
            )
        # Fresh event was inside the window; stale was not.
        assert report.delivered >= 1
        # No alerts on the fresh sample alone (no bounces inside window).
        assert all(
            "hard_bounce_rate" not in alert for alert in report.alerts
        )
    finally:
        await _cleanup(pool, ids)
