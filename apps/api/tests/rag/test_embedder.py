"""DeterministicEmbedder tests (no SDK contact)."""

from __future__ import annotations

import math

import pytest
from src.rag.base import EMBEDDING_DIM
from src.rag.embedder import DeterministicEmbedder, OpenAIEmbedder


async def test_deterministic_same_text_yields_same_vector() -> None:
    e = DeterministicEmbedder()
    a = await e.embed("hello world")
    b = await e.embed("hello world")
    assert a == b


async def test_deterministic_different_text_yields_different_vector() -> None:
    e = DeterministicEmbedder()
    a = await e.embed("hello world")
    b = await e.embed("Goodbye world")
    assert a != b


async def test_deterministic_vector_is_unit_length() -> None:
    e = DeterministicEmbedder()
    v = await e.embed("the quick brown fox")
    assert len(v) == EMBEDDING_DIM
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


async def test_deterministic_batch_matches_singles() -> None:
    e = DeterministicEmbedder()
    texts = ["one", "two", "three"]
    batch = await e.embed_batch(texts)
    singles = [await e.embed(t) for t in texts]
    assert batch == singles


async def test_deterministic_namespace_isolates_runs() -> None:
    a = DeterministicEmbedder(seed_namespace="suite_a")
    b = DeterministicEmbedder(seed_namespace="suite_b")
    va = await a.embed("hello")
    vb = await b.embed("hello")
    assert va != vb


def test_deterministic_validates_dim() -> None:
    with pytest.raises(ValueError):
        DeterministicEmbedder(dim=0)


def test_openai_embedder_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAIEmbedder(api_key="")
