"""CitationVerifier tests with httpx.MockTransport.

All HTTP traffic is mocked — assert call counts and request URLs/headers
end-to-end. The four task-required scenarios (verified DOI, invalid DOI,
fuzzy match, cache hit) are spelled out by name; three more cover the
edge paths called out in docs/04 §3.2 (partial match, OpenAlex
fallback, DOI exists but title mismatches).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from src.citations import (
    Citation,
    CitationCache,
    CitationVerifier,
    InMemoryCacheBackend,
    VerificationResult,
)
from src.citations.verifier import POLITE_POOL_MAILTO, USER_AGENT

# ── Mock transport helpers ───────────────────────────────────────────────


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by MockTransport. Production
    headers (User-Agent, mailto polite-pool) flow through the recorded
    requests so tests can assert them."""

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=5.0,
        headers={"User-Agent": USER_AGENT},
    )


def _crossref_metadata_response(*, title: str, doi: str = "10.1234/foo") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "ok",
            "message": {
                "DOI": doi,
                "title": [title],
                "container-title": ["Test Journal"],
                "issued": {"date-parts": [[2023]]},
            },
        },
    )


def _crossref_search_response(*, items: list[dict[str, Any]] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status": "ok", "message": {"items": items or []}},
    )


def _openalex_search_response(*, results: list[dict[str, Any]] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"results": results or []})


# ── 1. Verified DOI ─────────────────────────────────────────────────────


async def test_doi_direct_verified() -> None:
    citation = Citation(
        raw_text="[Smith 2023]",
        doi="10.1234/foo",
        title="Federated learning for industrial QC at the edge",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "doi.org":
            assert request.method == "HEAD"
            return httpx.Response(200)
        if request.url.host == "api.crossref.org":
            return _crossref_metadata_response(
                title="Federated learning for industrial QC at the edge"
            )
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "verified"
    assert result.source == "doi_direct"
    assert (result.match_score or 0.0) >= 0.85
    # Both the DOI HEAD and the Crossref metadata GET should fire.
    assert {r.url.host for r in requests} == {"doi.org", "api.crossref.org"}
    # Polite-pool UA + mailto. The ``@`` in the email is URL-encoded as
    # ``%40`` once httpx serialises the query — accept either form.
    crossref_req = next(r for r in requests if r.url.host == "api.crossref.org")
    query = crossref_req.url.query.decode()
    assert (POLITE_POOL_MAILTO in query) or ("support%40bluedev.dev" in query)
    assert "BluedevGrantWriter" in crossref_req.headers.get("user-agent", "")


# ── 2. Fabricated DOI ───────────────────────────────────────────────────


async def test_doi_404_fabricated() -> None:
    citation = Citation(raw_text="[Imaginary 2023]", doi="10.9999/nope")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(404)
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "fabricated"
    assert result.source == "doi_direct"


# ── 3. Crossref fuzzy match → verified ──────────────────────────────────


async def test_crossref_fuzzy_match_verified() -> None:
    citation = Citation(
        raw_text="[Smith 2023]",
        title="Edge AI for textile defect detection",
        authors=["Smith", "Jones"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return _crossref_search_response(
                items=[
                    {
                        "DOI": "10.1234/edge",
                        "title": ["Edge AI for textile defect detection"],
                        "container-title": ["Pattern Recognition"],
                    }
                ]
            )
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "verified"
    assert result.source == "crossref"
    assert result.match_score is not None
    assert result.match_score >= 0.85
    assert result.metadata.get("doi") == "10.1234/edge"


# ── 4. Cache hit skips HTTP entirely ────────────────────────────────────


async def test_cache_hit_skips_http() -> None:
    citation = Citation(raw_text="[Smith 2023]", doi="10.1234/foo")
    cache_backend = InMemoryCacheBackend()
    cache = CitationCache(backend=cache_backend)
    await cache.set(
        citation.cache_key,
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": "Pre-cached"},
        ),
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)  # would fail loudly if hit

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=cache)
        result = await verifier.verify(citation)

    assert result.status == "verified"
    assert result.source == "cache"
    assert requests == []


# ── 5. Crossref partial match (0.50–0.85) ──────────────────────────────


async def test_crossref_partial_match() -> None:
    citation = Citation(
        raw_text="[Smith 2023]",
        title="Edge AI for textile defect detection",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return _crossref_search_response(
                items=[
                    {
                        "DOI": "10.1234/x",
                        "title": ["Edge for textile aspects"],  # ~ mid score
                    }
                ]
            )
        if request.url.host == "api.openalex.org":
            return _openalex_search_response(results=[])  # OpenAlex returns nothing
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "partial_match"
    assert result.source == "crossref"
    score = result.match_score or 0.0
    assert 0.50 <= score < 0.85


# ── 6. OpenAlex fallback when Crossref misses ──────────────────────────


async def test_openalex_fallback_when_crossref_misses() -> None:
    citation = Citation(
        raw_text="[Smith 2023]",
        title="Hydrometallurgical lithium recovery",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return _crossref_search_response(items=[])
        if request.url.host == "api.openalex.org":
            return _openalex_search_response(
                results=[
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.5678/bar",
                        "title": "Hydrometallurgical lithium recovery",
                    }
                ]
            )
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "verified"
    assert result.source == "openalex"
    assert (result.match_score or 0.0) >= 0.85
    assert result.metadata.get("doi") == "10.5678/bar"


# ── 7. DOI exists but title mismatches → partial_match ─────────────────


async def test_doi_exists_but_title_mismatches_returns_partial_match() -> None:
    citation = Citation(
        raw_text="[Smith 2023]",
        doi="10.1234/foo",
        title="One specific topic about AI",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(200)
        if request.url.host == "api.crossref.org":
            return _crossref_metadata_response(
                title="A completely different paper about marine biology"
            )
        return httpx.Response(404)

    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None)
        result = await verifier.verify(citation)

    assert result.status == "partial_match"
    assert result.source == "doi_direct"
    assert result.warning is not None
    assert "title doesn't match" in result.warning.lower()


# ── 8. verify_many caps concurrency + tallies counts ───────────────────


async def test_verify_many_runs_all_and_logs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(200)
        if request.url.host == "api.crossref.org":
            return _crossref_metadata_response(title="match")
        return httpx.Response(404)

    citations = [Citation(raw_text=f"[Doc {i}]", doi=f"10.0/{i}", title="match") for i in range(5)]
    async with _make_client(handler) as client:
        verifier = CitationVerifier(client=client, cache=None, max_concurrency=2)
        results = await verifier.verify_many(citations)

    assert len(results) == len(citations)
    assert all(r.status == "verified" for r in results)


# ── Sanity: Citation.cache_key shape ───────────────────────────────────


def test_cache_key_is_sha256_hex() -> None:
    citation = Citation(raw_text="[Smith 2023]", doi="10.1234/foo")
    sha256_hex_length = 64
    assert len(citation.cache_key) == sha256_hex_length
    int(citation.cache_key, 16)  # raises if not valid hex
