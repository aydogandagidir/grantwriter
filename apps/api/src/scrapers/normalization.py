"""Shared normalization helpers for scrapers.

These functions turn the messy stuff funders publish — strings like
``"15 Ocak 2026"``, ``"€2-10M"``, ``"TRL 4-7"`` — into structured
:class:`~src.scrapers.base.NormalizedCall` fields. Every scraper should
call these instead of rolling its own regex; otherwise the dedupe layer
sees the same call twice with different normalized values and we end up
with duplicate rows.

All functions are pure (no I/O) and side-effect free, so they're trivial
to unit-test. The FX rate table is the only quasi-stateful piece — it's
a module-level dict that the runner can monkey-patch at startup if we
ever wire a live FX provider.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


# ── Currency / FX ────────────────────────────────────────────────────────

# Approximate cross rates against EUR, snapshot 2026-05. The scraper only
# needs ballpark figures — budgets in NormalizedCall are guidance for the
# matcher, not invoice amounts. Override via :func:`set_fx_rates` if you
# wire a live provider.
_FX_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "TRY": 0.028,  # ≈ 1 TL = €0.028
    "USD": 0.92,
    "GBP": 1.17,
    "CHF": 1.05,
}

# Funder-published amounts are ambiguous: "2M" can mean 2,000,000 or 2.0
# (millions of millions, lol). The unit map below converts the suffix to
# a multiplier; cope with both English and Turkish forms.
_AMOUNT_MULTIPLIERS: Final[dict[str, int]] = {
    "k": 1_000,
    "K": 1_000,
    "m": 1_000_000,
    "M": 1_000_000,
    "b": 1_000_000_000,
    "B": 1_000_000_000,
    "bin": 1_000,
    "milyon": 1_000_000,
    "milyar": 1_000_000_000,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}

_CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "₺": "TRY",
    "TL": "TRY",
    "tl": "TRY",
}


def set_fx_rates(rates: dict[str, float]) -> None:
    """Replace the FX table. Keys must include ``"EUR": 1.0``."""

    if rates.get("EUR") != 1.0:
        raise ValueError("FX table must anchor EUR to 1.0")
    _FX_TO_EUR.clear()
    _FX_TO_EUR.update(rates)


def to_eur(amount: float | None, currency: str) -> float | None:
    """Convert an amount in ``currency`` to EUR using the static table.

    Returns ``None`` when ``amount`` is ``None`` or the currency is
    unknown — the caller should treat the budget as missing rather than
    inventing a wrong number.
    """

    if amount is None:
        return None
    rate = _FX_TO_EUR.get(currency.upper())
    if rate is None:
        logger.warning("normalization_unknown_currency", extra={"currency": currency})
        return None
    return amount * rate


# Each end of a range gets its own optional unit because "500K – 1.5M TL"
# has different units on each side. Range delimiter is its own group
# (not a char class) so "5 to 10M" works without "to" matching as the
# characters t and o individually. Longer alternatives in the unit list
# come first so ``milyon`` doesn't get truncated to ``m``; ``\b`` after
# the unit prevents the same collapse from the other direction.
_BUDGET_PATTERN = re.compile(
    r"""
    (?P<symbol>[€$£₺]|TL|tl)?\s*
    (?P<low>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)
    \s*
    (?P<low_unit>billion|million|thousand|milyar|milyon|bin|K|M|B|k|m|b)?    (?:
      \s*(?:to|TO|ile|[-–—])\s*
      (?P<high>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)
      \s*
      (?P<high_unit>billion|million|thousand|milyar|milyon|bin|K|M|B|k|m|b)?    )?
    \s*
    (?P<currency>EUR|USD|GBP|TRY|TL|€|\$|£|₺)?
    """,
    re.VERBOSE,
)


def parse_budget_range(
    text: str, *, default_currency: str = "EUR"
) -> tuple[float | None, float | None, str]:
    """Best-effort parse of a budget string into ``(min, max, currency)``.

    Recognises forms like ``"€2-10M"``, ``"2.000.000 - 10.000.000 EUR"``,
    ``"500K – 1.5M TL"``, ``"€100k"``, ``"5 milyon TL"``. When only one
    number is present, ``min == max``. ``currency`` falls back to
    ``default_currency`` (typically the funder's home currency).

    Returns ``(None, None, default_currency)`` if no number is found.
    """

    if not text:
        return None, None, default_currency

    # Walk all candidate matches and pick the first that carries a real
    # currency signal — a symbol, unit suffix, or currency word. Walking
    # instead of taking ``search()`` first hit lets us skip non-budget
    # ranges like "TRL 3-5" that precede the actual budget in a topic
    # description ("…at TRL 3-5 with budget €2-8 million…").
    match = None
    low_unit: str | None = None
    high_unit: str | None = None
    symbol: str | None = None
    currency_token: str | None = None
    for candidate in _BUDGET_PATTERN.finditer(text):
        c_low_unit = candidate.group("low_unit")
        c_high_unit = candidate.group("high_unit")
        c_symbol = candidate.group("symbol")
        c_currency = candidate.group("currency")
        if not (c_symbol or c_low_unit or c_high_unit or c_currency):
            continue
        match = candidate
        low_unit, high_unit = c_low_unit, c_high_unit
        symbol, currency_token = c_symbol, c_currency
        break

    if match is None:
        return None, None, default_currency

    raw_low = _normalize_number(match.group("low"))
    raw_high_str = match.group("high")
    raw_high = _normalize_number(raw_high_str) if raw_high_str else None
    # When only one end declares a unit, that unit applies to both — for
    # "€2-10M" the M is on high; for "500K – 1.5M" each end has its own.
    fallback_unit = low_unit or high_unit
    low_mul = _AMOUNT_MULTIPLIERS.get(low_unit or fallback_unit or "", 1)
    high_mul = _AMOUNT_MULTIPLIERS.get(high_unit or fallback_unit or "", 1)

    low: float | None = raw_low * low_mul if raw_low is not None else None
    high: float | None = raw_high * high_mul if raw_high is not None else None

    currency_signal = symbol or currency_token
    if currency_signal:
        currency = _CURRENCY_SYMBOLS.get(currency_signal, currency_signal.upper())
    else:
        currency = default_currency

    if low is not None and high is None:
        high = low

    return low, high, currency


def _normalize_number(raw: str) -> float | None:
    """Parse a localised number string into a float.

    Handles ``"2,000,000"``, ``"2.000.000"``, ``"2 000 000"``, ``"2.5"``,
    ``"2,5"``. Heuristic: the final separator is the decimal mark **only**
    if there's exactly one separator total and 1–2 digits follow it.
    Anything else (multiple separators, 3+ digits after final separator)
    is treated as thousands grouping.
    """

    if not raw:
        return None
    cleaned = raw.strip().replace(" ", "")
    if not cleaned:
        return None

    dot_idx = cleaned.rfind(".")
    comma_idx = cleaned.rfind(",")
    last_sep_idx = max(dot_idx, comma_idx)

    if last_sep_idx == -1:
        try:
            return float(cleaned)
        except ValueError:
            return None

    digits_after = len(cleaned) - last_sep_idx - 1
    total_separators = cleaned.count(".") + cleaned.count(",")

    # Decimal mark only when there's exactly one separator AND 1-2 digits
    # follow it. "2.5" → 2.5, but "2.000" → 2000 (thousands), and
    # "2.000.000" → 2000000 (all thousands).
    if total_separators == 1 and 1 <= digits_after <= 2:
        integer_part = cleaned[:last_sep_idx].replace(".", "").replace(",", "")
        decimal_part = cleaned[last_sep_idx + 1:]
        try:
            return float(f"{integer_part}.{decimal_part}")
        except ValueError:
            return None

    # Otherwise, strip all separators as thousands grouping.
    bare = cleaned.replace(".", "").replace(",", "")
    try:
        return float(bare)
    except ValueError:
        return None


# ── TRL extraction ───────────────────────────────────────────────────────

# Range delimiter is its own non-capturing group so "to" / "ile" / "and"
# words don't collapse into a character class. The negative lookahead
# ``(?!\d)`` keeps "TRL 10" from matching "TRL 1" — implausible TRL
# values stay rejected.
_TRL_PATTERN = re.compile(
    r"""
    (?:
      TRL
      | Technology\s+Readiness\s+Level
      | Teknoloji\s+Haz[ıi]rl[ıi]k\s+Seviyesi
      | THS                                  # TR abbreviation
    )
    \s*
    (?P<low>[1-9])(?!\d)
    (?:
      \s*(?:to|TO|ile|and|[-–—])\s*
      (?P<high>[1-9])(?!\d)
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_trl_range(text: str) -> tuple[int | None, int | None]:
    """Pull a TRL range out of free-form text.

    Returns ``(min, max)`` with each between 1 and 9, or ``(None, None)``
    if nothing matched. Single-TRL mentions (``"TRL 7"``) return
    ``(7, 7)``.
    """

    if not text:
        return None, None
    match = _TRL_PATTERN.search(text)
    if not match:
        return None, None
    low = int(match.group("low"))
    high_raw = match.group("high")
    high = int(high_raw) if high_raw else low
    if not (1 <= low <= 9 and 1 <= high <= 9):
        return None, None
    if low > high:
        low, high = high, low
    return low, high


# ── Deadline parsing ─────────────────────────────────────────────────────

# Order matters: ISO first (most reliable), then numeric DMY, then verbose
# named-month forms. Each pattern captures named groups (y, m, d).
_DEADLINE_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"), "iso"),
    (re.compile(r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"), "dmy"),
    (
        re.compile(
            r"(?P<d>\d{1,2})\s+(?P<m_name>[A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(?P<y>\d{4})",
            re.IGNORECASE,
        ),
        "dmonth_y",
    ),
    (
        re.compile(
            r"(?P<m_name>[A-Za-z]+)\s+(?P<d>\d{1,2})(?:,?)\s+(?P<y>\d{4})",
            re.IGNORECASE,
        ),
        "month_dy",
    ),
]

_MONTH_NAMES: Final[dict[str, int]] = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # Turkish
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
    "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}


def parse_deadline(text: str) -> date | None:
    """Find the first plausible date in ``text``.

    Supports ISO (``2026-09-15``), DD/MM/YYYY (``15/09/2026``), and verbose
    forms in English (``September 15, 2026``) and Turkish (``15 Eylül 2026``).
    Returns ``None`` when nothing matches or the date is implausible
    (year < 2024 or > 2035).
    """

    if not text:
        return None
    for pattern, kind in _DEADLINE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            year = int(match.group("y"))
            day = int(match.group("d"))
            if kind in {"iso", "dmy"}:
                month = int(match.group("m"))
            else:
                month_name = match.group("m_name").lower()
                month = _MONTH_NAMES.get(month_name, 0)
                if month == 0:
                    continue
            if not (2024 <= year <= 2035):
                continue
            return date(year, month, day)
        except (ValueError, KeyError):
            continue
    return None


def compute_lifecycle_status(
    deadline: date | None,
    *,
    today: date | None = None,
    closing_soon_days: int = 14,
) -> str:
    """Decide where a call sits in its lifecycle, given the deadline.

    - No deadline: ``"open"`` (better assumption than ``"draft"`` for the
      manual seeding path).
    - Past deadline: ``"closed"``.
    - Within ``closing_soon_days``: ``"closing_soon"``.
    - Otherwise: ``"open"``.
    """

    if deadline is None:
        return "open"
    today = today or datetime.now(UTC).date()
    if deadline < today:
        return "closed"
    if (deadline - today).days <= closing_soon_days:
        return "closing_soon"
    return "open"


# ── URL canonicalisation ─────────────────────────────────────────────────

_TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src",
        "_ga", "_gl",
    }
)


def canonicalize_url(url: str) -> str:
    """Strip tracking params, lowercase scheme+host, sort query, drop fragment.

    Used as ``calls.source_url_canonical`` so cross-source dedup can match
    pages that differ only in UTMs.
    """

    if not url:
        return url
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower()
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    path = parts.path or "/"
    return urlunsplit((scheme, host, path, query, ""))


# ── Sector → NACE ────────────────────────────────────────────────────────

# Tiny seed map — extend as we encounter new sector phrases. NACE Rev.2
# (https://ec.europa.eu/eurostat/web/products-manuals-and-guidelines).
# Keys are lowercase; lookup is substring-based with longest-match wins.
_NACE_SEED: Final[dict[str, str]] = {
    # Manufacturing
    "automotive": "C29",
    "aerospace": "C30",
    "pharmaceutical": "C21",
    "biotech": "C21",
    "chemistry": "C20",
    "machinery": "C28",
    "electronics": "C26",
    "renewable energy": "D35",
    "solar": "D35",
    "wind energy": "D35",
    "energy storage": "D35",
    # Information & comms
    "software": "J62",
    "saas": "J62",
    "cybersecurity": "J62",
    "ai": "J62",
    "artificial intelligence": "J62",
    "machine learning": "J62",
    "cloud computing": "J63",
    "data": "J63",
    "telecom": "J61",
    # Health
    "healthcare": "Q86",
    "medical device": "C32",
    "diagnostics": "C32",
    # Construction / infra
    "construction": "F41",
    "smart city": "F42",
    "transportation": "H49",
    "logistics": "H52",
    # Agri
    "agritech": "A01",
    "agriculture": "A01",
    "food": "C10",
    # Education / R&D
    "education": "P85",
    "research": "M72",
}


def map_to_nace(sector_text: str) -> str | None:
    """Resolve a free-form sector phrase to its NACE Rev.2 code.

    Substring match — ``"software development"`` and ``"open-source software"``
    both map to ``"J62"``. Returns ``None`` when no seed phrase appears.

    The seed map is intentionally tiny; the matcher uses keyword overlap
    on top of this, so a missing mapping just means we won't get a
    NACE-level boost, not that the call disappears.
    """

    if not sector_text:
        return None
    lowered = sector_text.lower()
    best_match: tuple[str, str] | None = None
    for phrase, code in _NACE_SEED.items():
        if phrase in lowered and (best_match is None or len(phrase) > len(best_match[0])):
            best_match = (phrase, code)
    return best_match[1] if best_match else None


# ── Eligibility tag extraction ───────────────────────────────────────────

_ELIGIBILITY_SIGNALS: Final[dict[str, str]] = {
    # Order = priority. First match wins per category.
    "sme": "sme",
    "kobi": "sme",
    "küçük ve orta": "sme",
    "small and medium": "sme",
    "individual": "individual",
    "bireysel": "individual",
    "open to anyone": "individual",
    "university": "university",
    "üniversite": "university",
    "research organisation": "research_org",
    "research organization": "research_org",
    "araştırma kuruluşu": "research_org",
    "large company": "large_corp",
    "büyük işletme": "large_corp",
    "non-profit": "ngo",
    "nonprofit": "ngo",
    "ngo": "ngo",
    "consortium": "consortium_required",
    "konsorsiyum": "consortium_required",
    "at least 3 partners": "consortium_required",
    "minimum 3 partner": "consortium_required",
}


def extract_eligibility_tags(text: str) -> list[str]:
    """Return distinct eligibility tags discovered in ``text``.

    De-duplicated; case-insensitive. Caller is expected to layer on
    structured eligibility from the funder's machine-readable schema
    when available (EU F&T Portal API returns structured fields).
    """

    if not text:
        return []
    lowered = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for phrase, tag in _ELIGIBILITY_SIGNALS.items():
        if phrase in lowered and tag not in seen:
            found.append(tag)
            seen.add(tag)
    return found


__all__ = [
    "canonicalize_url",
    "compute_lifecycle_status",
    "extract_eligibility_tags",
    "extract_trl_range",
    "map_to_nace",
    "parse_budget_range",
    "parse_deadline",
    "set_fx_rates",
    "to_eur",
]
