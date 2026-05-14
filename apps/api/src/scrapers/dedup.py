"""Cross-source duplicate detection for scraped calls.

Two layers:

1. ``(source, external_id)`` UNIQUE constraint on ``calls`` (handled by the
   database). Re-running the same scraper against the same upstream call
   triggers ON CONFLICT and updates the existing row.
2. **Cross-source fuzzy dedup**: the same call may appear in both
   ``eu_ft_portal`` and ``cascade`` (a Cascade call announces an HE topic;
   NLnet themes sometimes show up on Cascade aggregators). This module
   detects those clashes after the row lands so an operator can review.

The fuzzy layer is intentionally *advisory*: it tags potential duplicates
in ``calls.raw_metadata.cross_source_duplicates`` rather than deleting,
so a human can confirm. Auto-merging is too risky — losing a row that
turns out to be a distinct sub-call is worse than carrying a duplicate
flag in the UI.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


# ── Distance helpers ─────────────────────────────────────────────────────


# Turkish characters that don't decompose under NFKD — "ı" (U+0131) and
# the dotless-i family aren't combining-char compositions, so the generic
# strip below misses them. We map them explicitly before NFKD.
_TURKISH_FOLDING = str.maketrans({
    "ı": "i", "İ": "I",
    "ş": "s", "Ş": "S",
    "ğ": "g", "Ğ": "G",
    "ç": "c", "Ç": "C",
    "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U",
})


def _strip_accents(text: str) -> str:
    """Map ``"İstanbul"`` → ``"Istanbul"`` so cross-language title compares work.

    Two-pass: first an explicit Turkish folding (since ``ı`` / ``İ`` don't
    decompose under NFKD), then generic combining-mark stripping for
    other Latin-1 accents.
    """

    folded = (text or "").translate(_TURKISH_FOLDING)
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", folded)
        if not unicodedata.combining(ch)
    )


def normalize_title(title: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace.

    Used as a dedup key. Two titles whose normalized forms are equal are
    candidates for the same call — even if one is in Turkish and the
    other in English of course they don't match, but minor punctuation
    differences ("NGI0 Core – Open Call" vs "NGI0 Core - Open call") do.
    """

    text = _strip_accents(title or "").lower()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def trigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity over character trigrams. Range [0, 1].

    Cheap pure-Python alternative to the ``pg_trgm`` extension's
    ``similarity()`` — we use it as a pre-filter before the SQL trigram
    query so the database doesn't get bombarded with comparisons for
    obviously different titles.
    """

    a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def trigrams(text: str) -> set[str]:
        padded = f"  {text}  "
        return {padded[i : i + 3] for i in range(len(padded) - 2)}

    set_a, set_b = trigrams(a), trigrams(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# ── Match candidate ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DuplicateCandidate:
    other_id: UUID
    other_source: str
    other_external_id: str
    other_title: str
    similarity: float
    """Trigram similarity of the normalized titles, [0, 1]."""
    deadline_delta_days: int | None
    """``|deadline_a - deadline_b|`` in days, or ``None`` if either side
    has no deadline."""


# ── Detection ────────────────────────────────────────────────────────────


async def find_cross_source_duplicates(
    conn: asyncpg.Connection,
    *,
    call_id: UUID,
    title: str,
    source: str,
    deadline: date | None,
    deadline_window_days: int = 7,
    similarity_threshold: float = 0.85,
) -> list[DuplicateCandidate]:
    """Return calls from *other* sources whose title trigram-similarity
    is above ``similarity_threshold`` and whose deadline falls within
    ``deadline_window_days`` of the new call (or one of them is null).

    Relies on the ``pg_trgm`` extension (already in migrations) and the
    ``idx_calls_text_trgm`` GIN index on ``title``.
    """

    if not title:
        return []

    if deadline is None:
        deadline_window_clause = ""
        params: list[Any] = [call_id, source, title, similarity_threshold]
    else:
        deadline_window_clause = """
            AND (
              c.deadline IS NULL
              OR (c.deadline BETWEEN $5::date AND $6::date)
            )
        """
        params = [
            call_id,
            source,
            title,
            similarity_threshold,
            deadline - timedelta(days=deadline_window_days),
            deadline + timedelta(days=deadline_window_days),
        ]

    rows = await conn.fetch(
        f"""
        SELECT c.id, c.source, c.external_id, c.title, c.deadline,
               similarity(c.title, $3) AS sim
          FROM calls c
         WHERE c.id <> $1
           AND c.source <> $2
           AND similarity(c.title, $3) >= $4
           {deadline_window_clause}
         ORDER BY sim DESC
         LIMIT 10
        """,
        *params,
    )

    candidates: list[DuplicateCandidate] = []
    for row in rows:
        other_deadline = row["deadline"]
        delta = (
            abs((deadline - other_deadline).days)
            if deadline and other_deadline
            else None
        )
        candidates.append(
            DuplicateCandidate(
                other_id=UUID(str(row["id"])),
                other_source=str(row["source"]),
                other_external_id=str(row["external_id"]),
                other_title=str(row["title"]),
                similarity=float(row["sim"]),
                deadline_delta_days=delta,
            )
        )
    return candidates


async def tag_duplicates(
    conn: asyncpg.Connection,
    *,
    call_id: UUID,
    duplicates: list[DuplicateCandidate],
) -> None:
    """Write candidate list into ``calls.raw_metadata.cross_source_duplicates``.

    Idempotent — overwrites any previous list under that key. We could
    union with the previous list, but the new run reflects the current
    state of the catalogue, so replacement is cleaner.
    """

    if not duplicates:
        await conn.execute(
            """
            UPDATE calls
               SET raw_metadata = raw_metadata - 'cross_source_duplicates'
             WHERE id = $1
            """,
            call_id,
        )
        return

    payload = [
        {
            "id": str(c.other_id),
            "source": c.other_source,
            "external_id": c.other_external_id,
            "title": c.other_title,
            "similarity": round(c.similarity, 3),
            "deadline_delta_days": c.deadline_delta_days,
        }
        for c in duplicates
    ]
    await conn.execute(
        """
        UPDATE calls
           SET raw_metadata = jsonb_set(
                 raw_metadata,
                 '{cross_source_duplicates}',
                 $2::jsonb,
                 true
               )
         WHERE id = $1
        """,
        call_id,
        json.dumps(payload),
    )
    logger.info(
        "scraper_cross_source_duplicates_tagged",
        extra={"call_id": str(call_id), "duplicate_count": len(payload)},
    )


__all__ = [
    "DuplicateCandidate",
    "find_cross_source_duplicates",
    "normalize_title",
    "tag_duplicates",
    "trigram_similarity",
]
