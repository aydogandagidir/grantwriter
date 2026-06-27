"""3-stage citation verifier per docs/04 §3.2.

Cascade:
    DOI present?
        YES → HEAD doi.org/<doi> → 200? GET crossref/works/<doi> → fuzzy
              title match ≥ verified_threshold → "verified"
                                            → 0.50–<0.85 → "partial_match"
              404 → "fabricated"
        NO  → Crossref search (title + first 2 authors)
              → best fuzzy score:
                  ≥ 0.85 → "verified"
                  0.50–<0.85 → "partial_match"
                  < 0.50 (or no items) → OpenAlex
                                          → ≥ 0.85 → "verified"
                                          → < 0.85 → "not_found"

Negative results (``fabricated`` / ``not_found``) are also cached — they
cost just as much to compute and are stable enough to reuse.

All HTTP goes through one shared ``httpx.AsyncClient`` so tests can
substitute :class:`httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

import httpx
from rapidfuzz import fuzz

from src.citations.base import Citation, VerificationResult
from src.citations.cache import CitationCache

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"
DOI_BASE_URL = "https://doi.org"
POLITE_POOL_MAILTO = "support@bluedev.dev"
USER_AGENT = f"BluedevGrantWriter/0.1 (mailto:{POLITE_POOL_MAILTO})"

VERIFIED_THRESHOLD = 0.85
PARTIAL_THRESHOLD = 0.50
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_CONCURRENCY = 8
DEFAULT_RETRY_MAX_ATTEMPTS = 3
_HTTP_NOT_FOUND = 404
_HTTP_CLIENT_ERROR_FLOOR = 400
_HTTP_RATE_LIMIT = 429

T = TypeVar("T")


def _normalise_doi(doi: str) -> str:
    """Strip the optional ``https://doi.org/`` prefix and lowercase the
    registrant prefix while preserving the suffix.

    DOIs are case-insensitive on the registrant side (the part before the
    first ``/``), case-sensitive on the suffix. Without normalisation,
    ``10.1234/Foo`` and ``10.1234/foo`` hash differently and miss the cache.
    """

    cleaned = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if "/" in cleaned:
        registrant, _, suffix = cleaned.partition("/")
        cleaned = f"{registrant.lower()}/{suffix}"
    return cleaned


def _title_score(citation_title: str | None, candidate_title: str | None) -> float:
    """Fuzzy title match on a 0–1 scale. Empty inputs → 0."""

    if not citation_title or not candidate_title:
        return 0.0
    return float(fuzz.token_set_ratio(citation_title, candidate_title)) / 100.0


def _crossref_query_string(citation: Citation) -> str:
    parts: list[str] = []
    if citation.title:
        parts.append(citation.title)
    if citation.authors:
        parts.extend(citation.authors[:2])
    return " ".join(p for p in parts if p).strip()


def _crossref_item_title(item: dict[str, Any]) -> str | None:
    title = item.get("title")
    if isinstance(title, list) and title:
        return str(title[0])
    if isinstance(title, str):
        return title
    return None


def _openalex_item_title(item: dict[str, Any]) -> str | None:
    title = item.get("title") or item.get("display_name")
    if isinstance(title, str):
        return title
    return None


# ── Retry helper ────────────────────────────────────────────────────────


async def _with_http_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_RETRY_MAX_ATTEMPTS,
    base_delay: float = 0.5,
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2**attempt) + random.uniform(0, 0.1))
        except httpx.HTTPStatusError as exc:
            # Rate-limited (429) → respect the retry budget.
            if exc.response.status_code != _HTTP_RATE_LIMIT:
                raise
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2**attempt) + random.uniform(0, 0.1))
    assert last_exc is not None
    raise last_exc


# ── Verifier ────────────────────────────────────────────────────────────


class CitationVerifier:
    """Runs the cascade. Stateless; safe to reuse across requests."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        cache: CitationCache | None = None,
        verified_threshold: float = VERIFIED_THRESHOLD,
        partial_threshold: float = PARTIAL_THRESHOLD,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        polite_pool_mailto: str = POLITE_POOL_MAILTO,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        self._cache = cache
        self._verified_threshold = verified_threshold
        self._partial_threshold = partial_threshold
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._mailto = polite_pool_mailto

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── Public API ──────────────────────────────────────────────────────

    async def verify(self, citation: Citation) -> VerificationResult:
        if self._cache is not None:
            cached = await self._cache.get(citation.cache_key)
            if cached is not None:
                return cached.model_copy(update={"source": "cache"})

        if citation.doi:
            result = await self._verify_via_doi(citation)
        else:
            result = await self._search_crossref(citation)
            if result.status == "not_found" or (
                result.status == "partial_match"
                and (result.match_score or 0.0) < self._partial_threshold
            ):
                # Crossref didn't help — try OpenAlex.
                openalex_result = await self._search_openalex(citation)
                if openalex_result.status in ("verified", "partial_match"):
                    result = openalex_result
                elif result.status == "partial_match":
                    # Keep Crossref's partial match if OpenAlex was no better.
                    pass
                else:
                    result = openalex_result

        if self._cache is not None:
            await self._cache.set(citation.cache_key, result)
        return result

    async def verify_many(self, citations: Sequence[Citation]) -> list[VerificationResult]:
        async def _one(c: Citation) -> VerificationResult:
            async with self._semaphore:
                return await self.verify(c)

        results = await asyncio.gather(*(_one(c) for c in citations))
        verified = sum(1 for r in results if r.status == "verified")
        fabricated = sum(1 for r in results if r.status in ("fabricated", "not_found"))
        logger.info(
            "citations_batch_verified",
            extra={
                "total": len(results),
                "verified": verified,
                "fabricated": fabricated,
            },
        )
        return results

    # ── Stage 1: DOI direct ─────────────────────────────────────────────

    async def _verify_via_doi(self, citation: Citation) -> VerificationResult:
        assert citation.doi is not None
        normalised_doi = _normalise_doi(citation.doi)

        head_check = await self._doi_head_check(normalised_doi)
        if head_check is not None:
            return head_check

        return await self._doi_metadata_check(citation, normalised_doi)

    async def _doi_head_check(self, normalised_doi: str) -> VerificationResult | None:
        """``HEAD doi.org/<doi>`` — short-circuits to fabricated/error
        without touching Crossref. Returns ``None`` if the DOI resolves
        cleanly so the caller proceeds to metadata fetch."""

        try:
            resp = await _with_http_retries(
                lambda: self._client.head(f"{DOI_BASE_URL}/{normalised_doi}", follow_redirects=True)
            )
        except httpx.HTTPError as exc:
            return VerificationResult(status="error", source="doi_direct", error=str(exc))
        if resp.status_code == _HTTP_NOT_FOUND:
            return VerificationResult(
                status="fabricated",
                source="doi_direct",
                metadata={"doi": normalised_doi},
            )
        if resp.status_code >= _HTTP_CLIENT_ERROR_FLOOR:
            return VerificationResult(
                status="error",
                source="doi_direct",
                error=f"DOI HEAD returned {resp.status_code}",
            )
        return None

    async def _doi_metadata_check(
        self, citation: Citation, normalised_doi: str
    ) -> VerificationResult:
        """``GET crossref/works/<doi>`` then fuzzy-match titles to
        confirm the DOI points at the cited paper. Returns ``verified``,
        ``partial_match``, or a no-metadata-but-DOI-exists ``verified``."""

        try:
            resp = await _with_http_retries(
                lambda: self._client.get(
                    f"{CROSSREF_URL}/{normalised_doi}",
                    params={"mailto": self._mailto},
                    headers={"User-Agent": USER_AGENT},
                )
            )
        except httpx.HTTPError as exc:
            return VerificationResult(status="error", source="doi_direct", error=str(exc))
        if resp.status_code >= _HTTP_CLIENT_ERROR_FLOOR:
            return VerificationResult(
                status="verified",
                source="doi_direct",
                metadata={"doi": normalised_doi},
                warning="DOI exists at doi.org but Crossref has no metadata.",
            )

        item = resp.json().get("message", {})
        candidate_title = _crossref_item_title(item)
        score = _title_score(citation.title, candidate_title)
        meta = {
            "doi": normalised_doi,
            "title": candidate_title,
            "container_title": (item.get("container-title") or [None])[0],
            "issued_year": _crossref_issued_year(item),
        }
        if score >= self._verified_threshold:
            return VerificationResult(
                status="verified",
                source="doi_direct",
                match_score=score,
                metadata=meta,
            )
        if not citation.title:
            return VerificationResult(
                status="verified",
                source="doi_direct",
                metadata=meta,
                warning="DOI verified; no citation title supplied for cross-check.",
            )
        return VerificationResult(
            status="partial_match",
            source="doi_direct",
            match_score=score,
            metadata=meta,
            warning="DOI exists but title doesn't match.",
        )

    # ── Stage 2: Crossref search ────────────────────────────────────────

    async def _search_crossref(self, citation: Citation) -> VerificationResult:
        query = _crossref_query_string(citation)
        if not query:
            return VerificationResult(status="not_found", source="crossref")
        try:
            response = await _with_http_retries(
                lambda: self._client.get(
                    CROSSREF_URL,
                    params={
                        "query.bibliographic": query,
                        "rows": 5,
                        "mailto": self._mailto,
                    },
                    headers={"User-Agent": USER_AGENT},
                )
            )
        except httpx.HTTPError as exc:
            return VerificationResult(status="error", source="crossref", error=str(exc))
        if response.status_code >= _HTTP_CLIENT_ERROR_FLOOR:
            return VerificationResult(
                status="error",
                source="crossref",
                error=f"Crossref returned {response.status_code}",
            )

        items = response.json().get("message", {}).get("items") or []
        return self._classify_search_hits(
            citation, items, source="crossref", title_extractor=_crossref_item_title
        )

    # ── Stage 3: OpenAlex fallback ──────────────────────────────────────

    async def _search_openalex(self, citation: Citation) -> VerificationResult:
        query = _crossref_query_string(citation)
        if not query:
            return VerificationResult(status="not_found", source="openalex")
        try:
            response = await _with_http_retries(
                lambda: self._client.get(
                    OPENALEX_URL,
                    params={
                        "search": query,
                        "per-page": 5,
                        "mailto": self._mailto,
                    },
                    headers={"User-Agent": USER_AGENT},
                )
            )
        except httpx.HTTPError as exc:
            return VerificationResult(status="error", source="openalex", error=str(exc))
        if response.status_code >= _HTTP_CLIENT_ERROR_FLOOR:
            return VerificationResult(
                status="error",
                source="openalex",
                error=f"OpenAlex returned {response.status_code}",
            )

        items = response.json().get("results") or []
        return self._classify_search_hits(
            citation, items, source="openalex", title_extractor=_openalex_item_title
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _classify_search_hits(
        self,
        citation: Citation,
        items: list[dict[str, Any]],
        *,
        source: str,
        title_extractor: Callable[[dict[str, Any]], str | None],
    ) -> VerificationResult:
        if not items:
            return VerificationResult(status="not_found", source=source)  # type: ignore[arg-type]

        best_score = 0.0
        best_item: dict[str, Any] | None = None
        for item in items:
            score = _title_score(citation.title, title_extractor(item))
            if score > best_score:
                best_score = score
                best_item = item
        if best_item is None:
            return VerificationResult(status="not_found", source=source)  # type: ignore[arg-type]

        meta = {
            "title": title_extractor(best_item),
            "doi": _normalise_doi(str(best_item.get("DOI") or best_item.get("doi") or "")) or None,
        }
        if best_score >= self._verified_threshold:
            return VerificationResult(
                status="verified",
                source=source,  # type: ignore[arg-type]
                match_score=best_score,
                metadata=meta,
            )
        if best_score >= self._partial_threshold:
            return VerificationResult(
                status="partial_match",
                source=source,  # type: ignore[arg-type]
                match_score=best_score,
                metadata=meta,
            )
        return VerificationResult(
            status="not_found",
            source=source,  # type: ignore[arg-type]
            match_score=best_score,
            metadata=meta,
        )


def _crossref_issued_year(item: dict[str, Any]) -> int | None:
    issued = item.get("issued") or {}
    parts = issued.get("date-parts") if isinstance(issued, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None


__all__ = [
    "CROSSREF_URL",
    "DEFAULT_MAX_CONCURRENCY",
    "DOI_BASE_URL",
    "OPENALEX_URL",
    "PARTIAL_THRESHOLD",
    "POLITE_POOL_MAILTO",
    "USER_AGENT",
    "VERIFIED_THRESHOLD",
    "CitationVerifier",
]
