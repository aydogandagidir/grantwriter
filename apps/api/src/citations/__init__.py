"""Citation verification subsystem.

The product's load-bearing differentiator (docs/04 §1) — every citation
verified against Crossref / OpenAlex before export, fabricated ones
block the export button. Public surface for the writer agents,
HallucinationHunter, the ``POST /api/v1/citations/{id}/verify`` endpoint,
and the batch Celery task.
"""

from __future__ import annotations

from src.citations.base import (
    Citation,
    VerificationResult,
    VerificationSource,
    VerificationStatus,
)
from src.citations.cache import (
    DEFAULT_TTL_SECONDS,
    CacheBackend,
    CitationCache,
    InMemoryCacheBackend,
    RedisCacheBackend,
)
from src.citations.extractors import extract_citations, parse_author_year
from src.citations.verifier import (
    PARTIAL_THRESHOLD,
    VERIFIED_THRESHOLD,
    CitationVerifier,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "PARTIAL_THRESHOLD",
    "VERIFIED_THRESHOLD",
    "CacheBackend",
    "Citation",
    "CitationCache",
    "CitationVerifier",
    "InMemoryCacheBackend",
    "RedisCacheBackend",
    "VerificationResult",
    "VerificationSource",
    "VerificationStatus",
    "extract_citations",
    "parse_author_year",
]
