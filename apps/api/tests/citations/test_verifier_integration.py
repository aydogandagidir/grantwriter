"""Integration tests for the citation verifier against the live
Crossref + OpenAlex APIs.

These tests are the only ones in the codebase that touch the public
internet on purpose. They live behind ``@pytest.mark.integration`` so
the fast CI lane skips them; opt in locally with
``pytest -m integration -v``.

Coverage:

- Known-good DOI (a widely-cited Crossref entry) resolves to
  ``status="verified"``.
- A deliberately-fabricated DOI string with valid syntax resolves to
  ``status="fabricated"`` or ``not_found``. We accept either because
  Crossref's behaviour for a never-issued DOI is to 404 → ``not_found``,
  and our cascade then asks OpenAlex (also 404) → falls through to a
  fabricated verdict. The contract is "must NOT be verified".

S3.D13.T1 ships this suite as a smoke gate operators can run before a
release to confirm Crossref hasn't broken the contract.

Why no mocks: the unit tests in ``tests/citations/test_verifier.py``
already exercise the verifier logic against scripted responses. This
suite asserts the live HTTP cascade still works — useful when Crossref
rolls a breaking change to their search API or rate-limits us mid-quarter.
"""

from __future__ import annotations

import httpx
import pytest
from src.citations import Citation, CitationVerifier

pytestmark = pytest.mark.integration


@pytest.fixture
async def live_verifier() -> CitationVerifier:
    """Construct a verifier wired to the real Crossref + OpenAlex API.

    No cache → every test exercises the full HTTP path. A polite-pool
    mailto would be nice in CI but the demo run only fires a few
    requests, so we accept the anonymous-pool rate limit.
    """

    client = httpx.AsyncClient(timeout=15.0)
    try:
        yield CitationVerifier(client=client, cache=None)
    finally:
        await client.aclose()


# Pinned to a Crossref-indexed paper that's been stable for years.
# Picked from the original LLaMA paper (Touvron et al., 2023) — high
# citation count, multiple corpora indexed, unlikely to disappear.
_KNOWN_GOOD_DOI = "10.48550/arXiv.2302.13971"


# Synthetic DOI shape: valid prefix, valid characters, but never
# registered. Crossref returns 404 and we expect the cascade to land
# on either ``fabricated`` or ``not_found`` after OpenAlex also says no.
_FAKE_DOI = "10.99999/bluedev-grantwriter-fabrication-canary-9z9z9z"


async def test_known_good_doi_verifies(live_verifier: CitationVerifier) -> None:
    """A real published paper MUST come back ``status="verified"``."""

    citation = Citation(
        raw_text="Touvron et al., 2023, LLaMA",
        doi=_KNOWN_GOOD_DOI,
        title="LLaMA: Open and Efficient Foundation Language Models",
        authors=["Touvron"],
        year=2023,
    )
    result = await live_verifier.verify(citation)

    assert result.status == "verified", (
        f"expected verified for a known-good DOI; "
        f"got status={result.status}, source={result.source}, "
        f"warning={result.warning}"
    )
    assert result.source in ("crossref", "openalex", "doi_direct"), (
        f"unexpected verification source: {result.source}"
    )


async def test_fabricated_doi_does_not_verify(live_verifier: CitationVerifier) -> None:
    """A syntactically-valid but never-registered DOI MUST NOT verify.

    The exact failure status (``fabricated`` vs ``not_found``) depends
    on whether OpenAlex finds anything for the title fallback; both are
    acceptable since the FE treats them identically (red badge).
    """

    citation = Citation(
        raw_text="A non-existent paper by no-one, 2099",
        doi=_FAKE_DOI,
        title="Synthetic paper for the Bluedev fabrication canary",
        authors=["NoSuchAuthor"],
        year=2099,
    )
    result = await live_verifier.verify(citation)

    assert result.status != "verified", (
        f"a fabricated DOI MUST NOT come back verified; "
        f"got status={result.status}, source={result.source}, "
        f"warning={result.warning}"
    )
    assert result.status in ("fabricated", "not_found", "partial_match", "error"), (
        f"unexpected status for fabricated DOI: {result.status}"
    )


async def test_no_doi_title_lookup_still_works(
    live_verifier: CitationVerifier,
) -> None:
    """Citations sometimes lack DOIs (book chapters, working papers).
    The verifier should fall back to Crossref's search-by-title and
    return SOME status without raising.
    """

    citation = Citation(
        raw_text="Touvron et al., 2023, LLaMA",
        doi=None,
        title="LLaMA: Open and Efficient Foundation Language Models",
        authors=["Touvron"],
        year=2023,
    )
    result = await live_verifier.verify(citation)
    # We don't pin the outcome — Crossref title search is fuzzy and the
    # paper has multiple OSF / arXiv mirrors. We just assert the call
    # round-trips without raising and the verifier reports a known
    # status enum.
    assert result.status in (
        "verified",
        "partial_match",
        "not_found",
        "fabricated",
        "error",
    ), f"unexpected status: {result.status}"
