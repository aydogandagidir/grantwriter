"""Embedders.

Two implementations behind the :class:`~src.rag.base.Embedder` protocol:

- :class:`OpenAIEmbedder` — calls ``text-embedding-3-large`` via the
  OpenAI Async SDK with batch + rate-limit-aware retry. The only place
  in the codebase that imports the openai SDK directly outside the LLM
  router (embeddings are a separate API surface; the router-style
  abstraction would over-engineer for one provider).
- :class:`DeterministicEmbedder` — hash-seeded unit vectors. Used by
  the offline seed script and the test suite. Same input always yields
  the same vector, so a query that exactly matches a chunk text scores
  cosine ~1.0.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

import openai

from src.rag.base import EMBEDDING_DIM

logger = logging.getLogger(__name__)

DEFAULT_MODEL: Final[str] = "text-embedding-3-large"
DEFAULT_BATCH_SIZE: Final[int] = 100
"""OpenAI embeddings cap is 2048 inputs / request, but smaller batches
keep latency tighter and limit blast radius on a 5xx."""

T = TypeVar("T")


async def _with_backoff(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    base_delay: float = 1.0,
    cap_delay: float = 16.0,
) -> T:
    """Exponential backoff for OpenAI rate-limit / transient 5xx."""

    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            delay = min(base_delay * (2**attempt), cap_delay) + random.uniform(0, 0.25)
            logger.warning(
                "embedder_retryable_error",
                extra={"attempt": attempt + 1, "delay": delay, "exc": type(exc).__name__},
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


class OpenAIEmbedder:
    """Production embedder backed by OpenAI ``text-embedding-3-large``."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key required")
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout)
        self._model = model
        self._batch_size = batch_size

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float]:
        result = await self.embed_batch([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]

            async def _call(b: list[str] = batch) -> Any:
                return await self._client.embeddings.create(model=self._model, input=b)

            response = await _with_backoff(_call)
            for item in response.data:
                vec = list(item.embedding)
                if len(vec) != EMBEDDING_DIM:
                    raise ValueError(
                        f"unexpected embedding dim from {self._model}: "
                        f"got {len(vec)}, expected {EMBEDDING_DIM}"
                    )
                out.append(vec)
        return out

    async def aclose(self) -> None:
        await self._client.close()


class DeterministicEmbedder:
    """Offline / test embedder.

    Returns deterministic 3072-dim unit vectors derived from a SHA-256
    hash of the input. Same text → same vector. Different texts give
    near-orthogonal vectors (random gaussian + L2 normalisation), which
    is good enough for "find your own content back" tests.

    Set ``seed_namespace`` to a unique-per-suite value so different
    test runs don't collide on the same vector space.
    """

    def __init__(self, *, dim: int = EMBEDDING_DIM, seed_namespace: str = "default") -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim
        self._seed_namespace = seed_namespace

    @property
    def model(self) -> str:
        return f"deterministic-{self._dim}d"

    async def embed(self, text: str) -> list[float]:
        return self._embed_one(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(f"{self._seed_namespace}::{text}".encode()).digest()
        # Use the digest as a seed; gauss draws are stable across Python
        # versions for a fixed Random instance.
        rng = random.Random(int.from_bytes(digest, "big"))
        v = [rng.gauss(0.0, 1.0) for _ in range(self._dim)]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MODEL",
    "DeterministicEmbedder",
    "OpenAIEmbedder",
]
