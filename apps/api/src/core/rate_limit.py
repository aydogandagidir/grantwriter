"""Per-user sliding-window rate limiting backed by Redis.

The limits in :data:`LLM_CALL`, :data:`LLM_CONFIG_TEST`, and
:data:`CITATION_VERIFY` come from docs/09 §8. The algorithm is the
classic sorted-set sliding-window log: every consume() drops members
older than the window, counts the survivors, and either inserts (if
under the limit) or computes when the oldest member ages out (the
``Retry-After`` value the route surfaces as a 429).

The whole check-and-increment runs in a single Lua script so two
parallel requests can never race past the cap. Lua scripts are
atomic in Redis — no MULTI/EXEC dance needed.

**Fail-open by design.** If ``REDIS_URL`` is unset (dev / smoke deploys)
or the Redis call itself fails, :meth:`RateLimiter.consume` returns
``allowed=True`` and logs a warning. Choosing fail-open keeps legit
traffic flowing through a Redis outage at the cost of letting an
attacker briefly burst — that's the right trade-off for a billing /
DOS shield, where availability beats strict enforcement.

The FastAPI integration is :func:`rate_limit`, a dependency factory:

    @router.post("/expensive")
    async def handler(
        rate: Annotated[RateLimitDecision, Depends(rate_limit(LLM_CALL))],
        ...,
    ) -> ...:
        # On exceeded, FastAPI never enters the body — the dep raises 429.
        ...
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from redis.exceptions import RedisError

from src.core.auth import CurrentUserId
from src.core.config import get_settings

logger = logging.getLogger(__name__)


# ── Rule catalog (docs/09 §8) ──────────────────────────────────────────


@dataclass(frozen=True)
class RateLimitRule:
    """One row of the limits table. ``name`` is the Redis-key segment."""

    name: str
    limit: int
    window_seconds: int


LLM_CALL = RateLimitRule(name="llm_call", limit=10, window_seconds=60)
"""Generate / validate — every burst hits the LLM provider."""

LLM_CONFIG_TEST = RateLimitRule(name="llm_config_test", limit=5, window_seconds=60)
"""BYOK key probe. Tighter than LLM_CALL because it makes a direct
provider round-trip with no caching, so the per-call cost is fixed
and an attacker amplifies rapidly."""

CITATION_VERIFY = RateLimitRule(name="citation_verify", limit=50, window_seconds=60)
"""Crossref/OpenAlex calls — cheap and cacheable, looser cap."""


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one consume(). Routes can read ``remaining`` to set
    ``X-RateLimit-Remaining`` headers on success responses."""

    allowed: bool
    remaining: int
    retry_after_seconds: float
    rule: RateLimitRule


# ── Atomic check-and-increment (Lua) ───────────────────────────────────


# Returns ``{allowed, remaining, retry_after_ms}`` (Lua tables index from 1
# but the redis-py async script wrapper unwraps the result into a list).
_LUA_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window + 1000)
  return {1, limit - count - 1, 0}
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_after_ms = 0
if #oldest >= 2 then
  retry_after_ms = (tonumber(oldest[2]) + window) - now
  if retry_after_ms < 0 then retry_after_ms = 0 end
end
return {0, 0, retry_after_ms}
"""


class RateLimiter:
    """Stateless wrapper around the Lua script.

    Construct once per app process with whatever Redis client you have
    (or ``None`` for the dev / no-Redis path). The instance is safe to
    share across coroutines — Redis connection-pool semantics provide
    the concurrency.
    """

    def __init__(self, redis_client: Any | None) -> None:
        self._redis = redis_client
        # ``register_script`` returns an AsyncScript that uses EVALSHA
        # then falls back to EVAL on NOSCRIPT — no need to retry by hand.
        self._script = (
            redis_client.register_script(_LUA_SCRIPT)
            if redis_client is not None
            else None
        )

    @property
    def enabled(self) -> bool:
        """``False`` when no Redis is wired — every consume() fail-opens."""

        return self._redis is not None and self._script is not None

    async def consume(
        self,
        *,
        identity: str,
        rule: RateLimitRule,
    ) -> RateLimitDecision:
        """One atomic check-and-increment. Never raises on Redis error.

        ``identity`` is whatever stable string identifies the actor
        (today: stringified user UUID; future: IP for unauthenticated
        routes). The Redis key namespaces by ``rule.name`` so different
        endpoints don't share buckets.
        """

        if not self.enabled:
            return RateLimitDecision(
                allowed=True,
                remaining=rule.limit,
                retry_after_seconds=0.0,
                rule=rule,
            )

        key = f"ratelimit:{rule.name}:{identity}"
        now_ms = int(time.time() * 1000)
        window_ms = rule.window_seconds * 1000
        # Unique member per call: same-millisecond callers must not clobber
        # each other's ZADD (the second ZADD would silently succeed without
        # updating the score, but counting still works — better safe).
        member = f"{now_ms}:{secrets.token_hex(4)}"

        try:
            assert self._script is not None  # narrowed by self.enabled
            result = await self._script(
                keys=[key],
                args=[now_ms, window_ms, rule.limit, member],
            )
        except RedisError:
            logger.warning(
                "rate_limit_redis_error_failing_open",
                extra={"rule": rule.name, "identity": identity},
            )
            return RateLimitDecision(
                allowed=True,
                remaining=rule.limit,
                retry_after_seconds=0.0,
                rule=rule,
            )

        allowed = bool(int(result[0]))
        remaining = int(result[1])
        retry_after_ms = int(result[2])
        return RateLimitDecision(
            allowed=allowed,
            remaining=remaining,
            retry_after_seconds=retry_after_ms / 1000.0,
            rule=rule,
        )


# ── Lazy app-state limiter + FastAPI dep factory ───────────────────────


async def _get_limiter(request: Request) -> RateLimiter:
    """Return the per-app :class:`RateLimiter`, creating it on first use.

    Reuses ``app.state.redis_client`` if the SSE pubsub already opened
    one (see ``proposals.py``); otherwise opens its own. The lifespan
    in ``main.py`` closes the shared client on shutdown.
    """

    cached = getattr(request.app.state, "rate_limiter", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        settings = get_settings()
        if settings.redis_url is not None:
            import redis.asyncio as redis_asyncio

            redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
                settings.redis_url.get_secret_value(),
                encoding="utf-8",
                decode_responses=False,
            )
            request.app.state.redis_client = redis_client

    limiter = RateLimiter(redis_client)
    request.app.state.rate_limiter = limiter
    if not limiter.enabled:
        logger.warning(
            "rate_limiter_disabled",
            extra={"reason": "REDIS_URL not configured — every request fail-opens"},
        )
    return limiter


_RateCheck = Callable[..., Coroutine[Any, Any, RateLimitDecision]]


def rate_limit(rule: RateLimitRule) -> _RateCheck:
    """FastAPI dep factory: build a Depends-able for a specific rule.

    Bind once at import time per route:

        Depends(rate_limit(LLM_CALL))

    The returned coroutine resolves user identity from the JWT, runs
    the consume(), and either raises 429 (with ``Retry-After``) or
    returns the :class:`RateLimitDecision` to the route — handy for
    setting ``X-RateLimit-Remaining`` on the success response.
    """

    async def _check(
        request: Request,
        user_id: CurrentUserId,
    ) -> RateLimitDecision:
        limiter = await _get_limiter(request)
        decision = await limiter.consume(identity=str(user_id), rule=rule)
        if not decision.allowed:
            # Round up so a sub-second window still tells the client to
            # wait at least 1s — a 0-second Retry-After is meaningless.
            retry_after_int = max(int(decision.retry_after_seconds + 0.999), 1)
            logger.info(
                "rate_limit_exceeded",
                extra={
                    "rule": rule.name,
                    "user_id": str(user_id),
                    "limit": rule.limit,
                    "window_seconds": rule.window_seconds,
                    "retry_after_seconds": decision.retry_after_seconds,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limit_exceeded",
                    "rule": rule.name,
                    "limit": rule.limit,
                    "window_seconds": rule.window_seconds,
                    "retry_after_seconds": decision.retry_after_seconds,
                },
                headers={
                    "Retry-After": str(retry_after_int),
                    "X-RateLimit-Limit": str(rule.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window": str(rule.window_seconds),
                },
            )
        return decision

    return _check


RateLimitDep = Annotated[RateLimitDecision, Depends]
"""Type alias used in route signatures — paired with ``Depends(rate_limit(RULE))``.

Example:

    rate: Annotated[RateLimitDecision, Depends(rate_limit(LLM_CALL))]
"""


def attach_rate_limit_headers(response: Any, decision: RateLimitDecision) -> None:
    """Set ``X-RateLimit-*`` headers on a successful response.

    Call from route handlers that want the FE to render "n / N requests
    used this minute". Optional — a 429 always carries them; success only
    needs them when the FE pre-renders the budget.
    """

    response.headers["X-RateLimit-Limit"] = str(decision.rule.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Window"] = str(decision.rule.window_seconds)


__all__ = [
    "CITATION_VERIFY",
    "LLM_CALL",
    "LLM_CONFIG_TEST",
    "RateLimitDecision",
    "RateLimitDep",
    "RateLimitRule",
    "RateLimiter",
    "attach_rate_limit_headers",
    "rate_limit",
]
