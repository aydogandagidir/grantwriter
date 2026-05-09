"""Cache layer for citation verifications.

30-day TTL per docs/04 §3.2. Two backends behind a single Protocol so
production wires Redis when ``REDIS_URL`` is set and dev / tests fall
back to an in-memory dict transparently.

Logged keys are SHA-256 hashes only — never the citation ``raw_text``
body, which can carry author names + affiliations (PII).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Protocol

from src.citations.base import VerificationResult

if TYPE_CHECKING:  # pragma: no cover — import only for type hints
    import redis.asyncio as redis_async

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
"""Tuned per docs/04 §3.2 — Crossref / OpenAlex metadata is stable
enough that 30 days is safe even for partial-match results."""


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


# ── Backends ────────────────────────────────────────────────────────────


class InMemoryCacheBackend:
    """Async-friendly dict with TTL-on-read.

    Used by tests and by dev when ``REDIS_URL`` is unset. Not safe across
    processes — production must use :class:`RedisCacheBackend`.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)


class RedisCacheBackend:
    """Wraps an ``redis.asyncio`` client with the same protocol shape."""

    def __init__(self, client: redis_async.Redis, *, key_prefix: str = "cite:") -> None:
        self._client = client
        self._key_prefix = key_prefix

    async def get(self, key: str) -> str | None:
        raw = await self._client.get(f"{self._key_prefix}{key}")
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(f"{self._key_prefix}{key}", value, ex=ttl_seconds)


# ── Façade ──────────────────────────────────────────────────────────────


class CitationCache:
    """Typed wrapper that serialises :class:`VerificationResult`."""

    def __init__(self, *, backend: CacheBackend, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._backend = backend
        self._ttl = ttl_seconds

    async def get(self, key: str) -> VerificationResult | None:
        raw = await self._backend.get(key)
        if raw is None:
            logger.debug("citation_cache_miss", extra={"key": key})
            return None
        try:
            result = VerificationResult.model_validate_json(raw)
        except ValueError:
            # Corrupted entry — log and treat as miss; the verifier will
            # repopulate. Don't raise: a bad cache row should not break
            # an otherwise-healthy verification.
            logger.warning("citation_cache_corrupt_entry", extra={"key": key})
            return None
        logger.debug("citation_cache_hit", extra={"key": key, "status": result.status})
        return result

    async def set(self, key: str, result: VerificationResult) -> None:
        await self._backend.set(key, result.model_dump_json(), self._ttl)
        logger.debug(
            "citation_cache_set",
            extra={"key": key, "status": result.status, "ttl_seconds": self._ttl},
        )


__all__ = [
    "CacheBackend",
    "CitationCache",
    "DEFAULT_TTL_SECONDS",
    "InMemoryCacheBackend",
    "RedisCacheBackend",
]
