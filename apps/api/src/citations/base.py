"""Citation domain types — shared across writer agents, the verifier,
the Hallucination Hunter, and the verify endpoint.

The verifier is the load-bearing concern; types here keep its inputs
and outputs typed end-to-end so the orchestrator + frontend agree on
``status`` shape (the export-block decision flips on it).
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerificationStatus = Literal[
    "unverified",
    "verifying",
    "verified",
    "fabricated",
    "partial_match",
    "not_found",
    "error",
]
VerificationSource = Literal["doi_direct", "crossref", "openalex", "manual", "cache"]


class Citation(BaseModel):
    """A single citation marker, raw + (optionally) parsed metadata.

    The writer agents emit them with just ``raw_text`` populated; the
    verifier fills out the other fields as it resolves the citation.
    Fields stay nullable so a not-yet-verified Citation round-trips
    through Pydantic cleanly.
    """

    model_config = ConfigDict(frozen=True)

    raw_text: str
    doi: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    verified: bool = False
    """Convenience for legacy writer-agent code; the canonical truth is
    ``VerificationResult.status``."""
    verification_source: VerificationSource | None = None

    @property
    def cache_key(self) -> str:
        """SHA-256 of (raw_text + (doi or "")) — stable across runs.

        Parsed author/year are NOT in the key: regex parsing is fuzzy,
        and including them would fragment the cache for the same
        underlying citation.
        """

        normalised_doi = (self.doi or "").lower().strip()
        return hashlib.sha256((self.raw_text + "\x00" + normalised_doi).encode("utf-8")).hexdigest()


class VerificationResult(BaseModel):
    """Outcome of running a :class:`Citation` through the verifier."""

    model_config = ConfigDict(frozen=True)

    status: VerificationStatus
    source: VerificationSource | None = None
    match_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form metadata returned by the upstream API — title,
    authors, journal, DOI when discovered, etc. Frontend renders it."""
    warning: str | None = None
    error: str | None = None


__all__ = [
    "Citation",
    "VerificationResult",
    "VerificationSource",
    "VerificationStatus",
]
