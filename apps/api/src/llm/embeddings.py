"""OpenAI embeddings client used by RAG, the loader script, and the
distinctiveness scorer.

Embeddings are deliberately separate from the chat LLM router (`router.py`,
not yet implemented). Single provider, no BYOK semantics, batchable — the
abstractions a chat router needs would be dead weight here.

The OpenAI batch endpoint accepts up to 2048 inputs per request; we stay
conservative at 100 to keep request size reasonable and to bound the blast
radius of any single 429.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from openai import APIStatusError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 100
_DEFAULT_CONCURRENCY = 10
_RETRYABLE = (RateLimitError, APIStatusError, asyncio.TimeoutError)


# Module-level singleton for the OpenAI client. Stored in a dict so we can
# reset it in tests without the `global` keyword (avoids PLW0603).
_state: dict[str, AsyncOpenAI | None] = {"client": None}


def _get_client() -> AsyncOpenAI:
    if _state["client"] is None:
        settings = get_settings()
        _state["client"] = AsyncOpenAI(api_key=settings.openai_api_key)
    client = _state["client"]
    assert client is not None  # narrows for mypy
    return client


def reset_client_for_tests() -> None:
    """Clear the cached client. Test fixtures call this after monkeypatching."""
    _state["client"] = None


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def _embed_request(texts: Sequence[str], model: str) -> list[list[float]]:
    client = _get_client()
    response = await client.embeddings.create(model=model, input=list(texts))
    return [item.embedding for item in response.data]


async def embed(text: str, *, model: str | None = None) -> list[float]:
    """Embed a single string. Uses the configured embedding model by default."""
    settings = get_settings()
    chosen = model or settings.embedding_model
    [vector] = await _embed_request([text], chosen)
    return vector


async def embed_batch(
    texts: Sequence[str],
    *,
    model: str | None = None,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> list[list[float]]:
    """Embed many strings concurrently in batches. Order is preserved.

    Uses an asyncio Semaphore to cap concurrent in-flight requests at
    `concurrency`; each request bundles up to `batch_size` inputs to OpenAI's
    batched embeddings endpoint.
    """
    if not texts:
        return []
    settings = get_settings()
    chosen = model or settings.embedding_model
    sem = asyncio.Semaphore(concurrency)
    batches: list[list[str]] = [
        list(texts[i : i + batch_size]) for i in range(0, len(texts), batch_size)
    ]

    async def run_batch(idx: int, batch: list[str]) -> tuple[int, list[list[float]]]:
        async with sem:
            logger.debug("embedding batch %d (%d items)", idx, len(batch))
            return idx, await _embed_request(batch, chosen)

    tasks = [run_batch(i, b) for i, b in enumerate(batches)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    flat: list[list[float]] = []
    for _, vectors in results:
        flat.extend(vectors)
    return flat
