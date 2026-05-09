"""Cache backend + façade tests."""

from __future__ import annotations

from src.citations.base import Citation, VerificationResult
from src.citations.cache import (
    DEFAULT_TTL_SECONDS,
    CitationCache,
    InMemoryCacheBackend,
)


async def test_inmemory_round_trip() -> None:
    cache = CitationCache(backend=InMemoryCacheBackend())
    result = VerificationResult(status="verified", source="crossref", match_score=0.97)
    citation = Citation(raw_text="[Smith 2023]")

    await cache.set(citation.cache_key, result)
    fetched = await cache.get(citation.cache_key)
    assert fetched is not None
    assert fetched.status == "verified"
    assert fetched.source == "crossref"
    assert fetched.match_score == result.match_score


async def test_inmemory_miss_returns_none() -> None:
    cache = CitationCache(backend=InMemoryCacheBackend())
    assert await cache.get("does-not-exist") is None


async def test_ttl_expiry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The in-memory backend uses time.monotonic(); patch it to a list
    of values and assert post-expiry returns None."""

    import src.citations.cache as cache_module

    backend = InMemoryCacheBackend()
    cache = CitationCache(backend=backend, ttl_seconds=10)

    fake_clock = [1000.0]
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: fake_clock[0])

    citation = Citation(raw_text="[Smith 2023]")
    await cache.set(
        citation.cache_key,
        VerificationResult(status="verified"),
    )
    # Just before expiry — still hot.
    fake_clock[0] = 1009.999
    assert await cache.get(citation.cache_key) is not None
    # After expiry — gone.
    fake_clock[0] = 1010.001
    assert await cache.get(citation.cache_key) is None


def test_cache_key_stable_across_runs() -> None:
    a = Citation(raw_text="[Smith 2023]", doi="10.1234/foo")
    b = Citation(raw_text="[Smith 2023]", doi="10.1234/foo")
    assert a.cache_key == b.cache_key


def test_cache_key_includes_doi_normalisation() -> None:
    """Same raw_text, different DOI → different keys."""

    a = Citation(raw_text="[Smith 2023]", doi="10.1234/foo")
    b = Citation(raw_text="[Smith 2023]", doi="10.5678/bar")
    assert a.cache_key != b.cache_key


def test_default_ttl_is_30_days() -> None:
    seconds_per_day = 24 * 60 * 60
    days = 30
    assert days * seconds_per_day == DEFAULT_TTL_SECONDS


async def test_corrupt_entry_treated_as_miss() -> None:
    """A corrupted JSON blob in the cache must not raise — the
    verifier should re-resolve and overwrite."""

    backend = InMemoryCacheBackend()
    await backend.set("k", "not-valid-json", DEFAULT_TTL_SECONDS)
    cache = CitationCache(backend=backend)
    assert await cache.get("k") is None
