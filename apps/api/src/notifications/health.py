"""Sender-reputation health monitor.

Reads ``email_events`` (populated by the Resend webhook receiver) over
a rolling window and produces a structured report — hard-bounce rate,
soft-bounce rate, complaint rate, delivered count, sample size. The
report is the input the ops CLI (and any future admin UI) uses to
decide whether the sender domain is at risk.

Threshold defaults match standard sender-reputation guidance:

- **Hard bounce > 2 %** — Resend / SES start throttling at 5 %; we
  alert earlier so the operator has time to act.
- **Complaint > 0.1 %** — most ESPs treat 0.1 % as the hard ceiling.
- **Sample size < 50** — too noisy to alert on; the run reports the
  numbers but doesn't trip the threshold.

The analyzer is pure (in-memory dataclasses) so the CLI + tests can
exercise it without a DB. The DB-bound :func:`evaluate_health` is a
thin wrapper that pulls rows and feeds them in.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


DEFAULT_WINDOW = timedelta(hours=1)
DEFAULT_HARD_BOUNCE_THRESHOLD = 0.02  # 2 %
DEFAULT_COMPLAINT_THRESHOLD = 0.001  # 0.1 %
DEFAULT_MIN_SAMPLE = 50


@dataclass(frozen=True)
class EmailEventLite:
    """In-memory shape the analyzer needs from each event row."""

    event_type: str
    bounce_type: str | None  # 'hard' / 'soft' / None for non-bounce events


@dataclass(frozen=True)
class HealthReport:
    """The full breakdown — safe to ship as JSON to the operator."""

    window_seconds: int
    total_events: int
    delivered: int
    hard_bounces: int
    soft_bounces: int
    complaints: int
    other: int
    hard_bounce_rate: float
    complaint_rate: float
    sample_size: int  # delivered + bounces + complaints (excludes opens/clicks)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "total_events": self.total_events,
            "delivered": self.delivered,
            "hard_bounces": self.hard_bounces,
            "soft_bounces": self.soft_bounces,
            "complaints": self.complaints,
            "other": self.other,
            "hard_bounce_rate": round(self.hard_bounce_rate, 6),
            "complaint_rate": round(self.complaint_rate, 6),
            "sample_size": self.sample_size,
            "alerts": list(self.alerts),
        }


@dataclass(frozen=True)
class HealthThresholds:
    hard_bounce: float = DEFAULT_HARD_BOUNCE_THRESHOLD
    complaint: float = DEFAULT_COMPLAINT_THRESHOLD
    min_sample: int = DEFAULT_MIN_SAMPLE


# ── Pure analyzer ──────────────────────────────────────────────────────


def analyze(
    events: Iterable[EmailEventLite],
    *,
    window: timedelta = DEFAULT_WINDOW,
    thresholds: HealthThresholds | None = None,
) -> HealthReport:
    """Compute the report from a flat iterable of events.

    Multiple events for the same recipient (e.g. delivered + bounced
    if Resend retries) are NOT deduped — the analyzer cares about
    event volume, not per-recipient outcome.
    """

    th = thresholds or HealthThresholds()
    counter: Counter[str] = Counter()
    hard = soft = 0
    for ev in events:
        counter[ev.event_type] += 1
        if ev.event_type == "email.bounced":
            if (ev.bounce_type or "").lower() == "hard":
                hard += 1
            else:
                soft += 1

    delivered = counter.get("email.delivered", 0)
    complaints = counter.get("email.complained", 0)
    total = sum(counter.values())
    sample_size = delivered + hard + soft + complaints
    other = total - sample_size

    hard_rate = (hard / sample_size) if sample_size > 0 else 0.0
    complaint_rate = (complaints / sample_size) if sample_size > 0 else 0.0

    alerts: list[str] = []
    if sample_size >= th.min_sample:
        if hard_rate > th.hard_bounce:
            alerts.append(
                f"hard_bounce_rate {hard_rate:.4f} exceeds threshold {th.hard_bounce}"
            )
        if complaint_rate > th.complaint:
            alerts.append(
                f"complaint_rate {complaint_rate:.4f} exceeds threshold {th.complaint}"
            )

    return HealthReport(
        window_seconds=int(window.total_seconds()),
        total_events=total,
        delivered=delivered,
        hard_bounces=hard,
        soft_bounces=soft,
        complaints=complaints,
        other=other,
        hard_bounce_rate=hard_rate,
        complaint_rate=complaint_rate,
        sample_size=sample_size,
        alerts=alerts,
    )


# ── DB-bound evaluator ─────────────────────────────────────────────────


def _payload_to_bounce_type(payload_text: str | None) -> str | None:
    if not payload_text:
        return None
    try:
        decoded = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    data = decoded.get("data")
    if not isinstance(data, dict):
        return None
    bounce = data.get("bounce")
    if not isinstance(bounce, dict):
        return None
    bounce_type = bounce.get("type")
    return str(bounce_type) if bounce_type else None


async def evaluate_health(
    conn: asyncpg.Connection,
    *,
    window: timedelta = DEFAULT_WINDOW,
    thresholds: HealthThresholds | None = None,
) -> HealthReport:
    """Pull the window's events, run :func:`analyze`, log if any alert fires."""

    rows = await conn.fetch(
        """
        select event_type, payload::text as payload_text
          from email_events
         where received_at >= now() - $1::interval
        """,
        window,
    )
    events = (
        EmailEventLite(
            event_type=str(row["event_type"]),
            bounce_type=_payload_to_bounce_type(row["payload_text"]),
        )
        for row in rows
    )
    report = analyze(events, window=window, thresholds=thresholds)
    if report.alerts:
        logger.warning(
            "email_health_threshold_tripped",
            extra={
                "alerts": report.alerts,
                "sample_size": report.sample_size,
                "window_seconds": report.window_seconds,
                "hard_bounce_rate": round(report.hard_bounce_rate, 6),
                "complaint_rate": round(report.complaint_rate, 6),
            },
        )
    else:
        logger.info(
            "email_health_ok",
            extra={
                "sample_size": report.sample_size,
                "window_seconds": report.window_seconds,
            },
        )
    return report


__all__ = [
    "DEFAULT_COMPLAINT_THRESHOLD",
    "DEFAULT_HARD_BOUNCE_THRESHOLD",
    "DEFAULT_MIN_SAMPLE",
    "DEFAULT_WINDOW",
    "EmailEventLite",
    "HealthReport",
    "HealthThresholds",
    "analyze",
    "evaluate_health",
]
