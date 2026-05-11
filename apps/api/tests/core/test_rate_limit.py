"""Unit tests for :mod:`src.core.rate_limit`.

Covers the :class:`RateLimiter` directly against a real Redis (the
docker-compose stack), plus the fail-open path when no client is wired.
The route-level 429 integration test lives in
``tests/api/test_rate_limit_integration.py`` to keep the unit suite fast.

Tests skip when ``TEST_REDIS_URL`` is unset — the local fast lane has no
broker. CI sets it to the same Redis container that the rest of the
suite uses.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from src.core.rate_limit import (
    LLM_CALL,
    RateLimitDecision,
    RateLimiter,
    RateLimitRule,
)


def _test_redis_url() -> str | None:
    return os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def redis_url() -> str:
    url = _test_redis_url()
    if not url:
        pytest.skip("TEST_REDIS_URL/REDIS_URL not set — skipping rate-limit tests")
    return url


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[object]:
    import redis.asyncio as redis_async

    client = redis_async.from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def limiter(redis_client: object) -> RateLimiter:
    return RateLimiter(redis_client)


@pytest.fixture
def fresh_identity() -> str:
    """Per-test unique identity so tests don't share buckets."""

    return f"test-user-{uuid.uuid4()}"


# ── Disabled-limiter (no Redis) path ───────────────────────────────────


def test_disabled_limiter_reports_disabled() -> None:
    limiter = RateLimiter(redis_client=None)
    assert limiter.enabled is False


async def test_disabled_limiter_always_allows() -> None:
    """Without Redis, every consume() is allowed and reports full budget."""

    limiter = RateLimiter(redis_client=None)
    decision = await limiter.consume(identity="anyone", rule=LLM_CALL)
    assert decision.allowed is True
    assert decision.remaining == LLM_CALL.limit
    assert decision.retry_after_seconds == 0.0
    assert decision.rule == LLM_CALL


# ── Sliding-window happy path ──────────────────────────────────────────


async def test_first_call_is_allowed_and_decrements_remaining(
    limiter: RateLimiter, fresh_identity: str
) -> None:
    decision = await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
    assert decision.allowed is True
    assert decision.remaining == LLM_CALL.limit - 1


async def test_consuming_to_the_limit_then_one_more_returns_429(
    limiter: RateLimiter, fresh_identity: str
) -> None:
    """The Nth call is allowed (remaining = 0); the (N+1)th is rejected."""

    decisions = [
        await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
        for _ in range(LLM_CALL.limit)
    ]
    assert all(d.allowed for d in decisions)
    assert decisions[-1].remaining == 0

    rejected = await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
    assert rejected.allowed is False
    assert rejected.remaining == 0
    # Window is 60s; first member just inserted, so retry-after must be
    # close to (but less than) 60s.
    assert 0 < rejected.retry_after_seconds <= LLM_CALL.window_seconds


async def test_separate_identities_do_not_share_buckets(
    limiter: RateLimiter,
) -> None:
    user_a = f"a-{uuid.uuid4()}"
    user_b = f"b-{uuid.uuid4()}"

    # Burn user A's quota completely.
    for _ in range(LLM_CALL.limit):
        await limiter.consume(identity=user_a, rule=LLM_CALL)
    a_blocked = await limiter.consume(identity=user_a, rule=LLM_CALL)
    assert a_blocked.allowed is False

    # User B is untouched.
    b_first = await limiter.consume(identity=user_b, rule=LLM_CALL)
    assert b_first.allowed is True
    assert b_first.remaining == LLM_CALL.limit - 1


async def test_separate_rules_do_not_share_buckets(
    limiter: RateLimiter, fresh_identity: str
) -> None:
    """Burning LLM_CALL must not affect a different rule for the same user."""

    other = RateLimitRule(name="probe_only", limit=3, window_seconds=60)
    for _ in range(LLM_CALL.limit):
        await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
    blocked = await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
    assert blocked.allowed is False

    # A different rule's bucket is empty.
    other_decision = await limiter.consume(identity=fresh_identity, rule=other)
    assert other_decision.allowed is True
    assert other_decision.remaining == other.limit - 1


async def test_window_expiry_resets_the_bucket(
    limiter: RateLimiter, fresh_identity: str
) -> None:
    """A 1-second-window rule lets us actually wait it out without flake.

    Using a real ``asyncio.sleep`` here is a tradeoff — it costs ~1.2s of
    test time. Worth it for end-to-end confidence in the ZREMRANGEBYSCORE
    half of the algorithm.
    """

    short = RateLimitRule(name=f"short-{uuid.uuid4()}", limit=2, window_seconds=1)

    a = await limiter.consume(identity=fresh_identity, rule=short)
    b = await limiter.consume(identity=fresh_identity, rule=short)
    c = await limiter.consume(identity=fresh_identity, rule=short)
    assert a.allowed and b.allowed
    assert c.allowed is False

    # Wait past the window edge — Redis must garbage-collect the old
    # members and let the next call through.
    await asyncio.sleep(1.2)
    after = await limiter.consume(identity=fresh_identity, rule=short)
    assert after.allowed is True
    assert after.remaining == short.limit - 1


async def test_decision_dataclass_carries_the_rule(
    limiter: RateLimiter, fresh_identity: str
) -> None:
    """``RateLimitDecision.rule`` is what attach_rate_limit_headers reads."""

    decision = await limiter.consume(identity=fresh_identity, rule=LLM_CALL)
    assert isinstance(decision, RateLimitDecision)
    assert decision.rule.name == LLM_CALL.name
    assert decision.rule.limit == LLM_CALL.limit
    assert decision.rule.window_seconds == LLM_CALL.window_seconds


# ── Fail-open on Redis errors ──────────────────────────────────────────


class _BrokenRedis:
    """Stand-in client whose script always raises — exercises the fail-open path."""

    def register_script(self, _: str) -> _BrokenScript:
        return _BrokenScript()


class _BrokenScript:
    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        from redis.exceptions import RedisError

        raise RedisError("synthetic outage")


async def test_redis_failure_fails_open_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="src.core.rate_limit")

    limiter = RateLimiter(_BrokenRedis())
    decision = await limiter.consume(identity="user-x", rule=LLM_CALL)
    assert decision.allowed is True
    assert decision.remaining == LLM_CALL.limit

    # And we made it visible — silent fail-open would hide a real outage.
    assert any(
        "rate_limit_redis_error_failing_open" in record.getMessage()
        for record in caplog.records
    )
