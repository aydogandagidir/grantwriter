"""IdeaGenerator — turn one open call into 3-5 concrete project ideas.

The 'Browse calls → generate ideas' half of bidirectional matching:
the user lands on a call they like but doesn't have a project in mind,
so we draft a slate of distinct, call-aligned project ideas they can
pick from (or use as a starting point for their own).

V1 pipeline:

  1. Cache check — ``call_idea_suggestions`` is a cross-tenant cache
     (no PII in the output; the seed material is the funder's public
     call text). A cache hit within ``CACHE_TTL_DAYS`` skips the LLM
     entirely, so the second tenant to browse a call pays nothing.
  2. Fetch the call + (optional) the requesting tenant's org profile —
     the profile lets us bias ideas toward the org's sectors / TRL /
     expertise rather than producing generic slates.
  3. LLM generation (Opus, task=``idea_generator``, temperature 0.6)
     produces 3-5 distinct ideas as structured JSON.
  4. Persist into ``call_idea_suggestions``.

V2 adds: RAG retrieval of similar funded projects from
``successful_proposals_corpus`` + ``cordis_funded_projects`` to ground
the slate, and a DistinctivenessScorer pass to drop ideas too close to
already-funded work. Both are noted as TODO seams below.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


DEFAULT_N_IDEAS = 3
MAX_N_IDEAS = 5
CACHE_TTL_DAYS = 7
GENERATOR_VERSION = "idea_generator-v1"


# ── Result dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class GeneratedIdea:
    """One project idea card produced for a call."""

    title: str
    abstract: str
    technology_angle: str
    impact_thesis: str
    est_budget_eur_min: float | None
    est_budget_eur_max: float | None
    est_trl: int | None
    suggested_consortium_type: str
    alignment_score: float
    distinctiveness_score: float | None = None


@dataclass(frozen=True)
class IdeaGenerationResult:
    call_id: UUID
    ideas: list[GeneratedIdea]
    generated_at: str
    generator_version: str = GENERATOR_VERSION
    from_cache: bool = False


@dataclass(frozen=True)
class _CallContext:
    call_id: UUID
    programme_id: str
    agency_id: str | None
    title: str
    scope_summary: str | None
    call_text: str | None
    topic_keywords: list[str]
    sectors: list[str]
    trl_min: int | None
    trl_max: int | None
    budget_min_eur: float | None
    budget_max_eur: float | None
    eligibility_tags: list[str]
    language: str


@dataclass(frozen=True)
class _OrgPriors:
    """Optional bias input — the requesting tenant's profile."""

    entity_type: str | None = None
    country: str | None = None
    sectors: list[str] = field(default_factory=list)
    technology_areas: list[str] = field(default_factory=list)
    trl_current: int | None = None
    expertise_keywords: list[str] = field(default_factory=list)


# ── LLM prompt ───────────────────────────────────────────────────────────

_SYSTEM = """\
You generate concrete, fundable project ideas for a specific grant call.

Produce ideas that are:
  - DISTINCT from each other — different technical angles, not variations
    on one theme.
  - ALIGNED with the call's scope, topic keywords, TRL band, and budget.
  - GROUNDED — each must name a real technical approach, not a buzzword
    soup. An evaluator should be able to picture the work.
  - HONEST about consortium needs — if the call requires a consortium,
    say what kind of partners the idea needs.

When an organization profile is provided, bias the slate toward that
org's sectors, technology areas, and TRL level — but don't force a bad
fit; a strong generic idea beats a weak on-profile one.

Return ONLY a single JSON object, no markdown, no commentary:

{
  "ideas": [
    {
      "title": "...",                       // <= 120 chars
      "abstract": "...",                    // 120-400 words, the project
      "technology_angle": "...",            // the specific technical approach
      "impact_thesis": "...",               // why this matters, who benefits
      "est_budget_eur_min": 1500000,        // number or null
      "est_budget_eur_max": 3000000,        // number or null
      "est_trl": 5,                         // 1-9 or null
      "suggested_consortium_type": "...",   // e.g. "SME lead + 1 RTO + 1 end-user"
      "alignment_score": 0.85               // 0-1, your honest call-fit estimate
    }
  ]
}

Generate exactly the number of ideas requested. Write the abstract in
the call's language (Turkish for tr calls, English for en calls).
"""


# ── Generator ────────────────────────────────────────────────────────────


class IdeaGenerator:
    """Generate (or cache-serve) a slate of project ideas for one call."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        router: LLMRouter,
        tenant_id: UUID,
    ) -> None:
        self._pool = pool
        self._router = router
        self._tenant_id = tenant_id

    async def generate(
        self,
        call_id: UUID,
        *,
        n_ideas: int = DEFAULT_N_IDEAS,
        org_profile_tenant_id: UUID | None = None,
        force_refresh: bool = False,
    ) -> IdeaGenerationResult:
        """Return 3-5 generated ideas for ``call_id``.

        Serves from ``call_idea_suggestions`` when a fresh cache entry
        exists unless ``force_refresh``. ``org_profile_tenant_id``, when
        given, biases the slate toward that tenant's profile — but the
        cache is keyed on call only, so a profile-biased request always
        regenerates (it can't reuse a generic cached slate).
        """

        capped_n = max(1, min(n_ideas, MAX_N_IDEAS))

        if not force_refresh and org_profile_tenant_id is None:
            cached = await self._read_cache(call_id)
            if cached is not None:
                return cached

        call_ctx = await self._load_call(call_id)
        org_priors = (
            await self._load_org_priors(org_profile_tenant_id)
            if org_profile_tenant_id is not None
            else None
        )

        ideas = await self._llm_generate(call_ctx, org_priors, capped_n)

        # Only cache the generic (no-profile) slate — profile-biased
        # output is tenant-specific and would poison the shared cache.
        if org_profile_tenant_id is None and ideas:
            await self._persist_cache(call_id, ideas)

        return IdeaGenerationResult(
            call_id=call_id,
            ideas=ideas,
            generated_at=_now_iso(),
            from_cache=False,
        )

    # ── Cache ────────────────────────────────────────────────────────

    async def _read_cache(self, call_id: UUID) -> IdeaGenerationResult | None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT title, abstract, technology_angle, impact_thesis,
                       est_budget_eur_min, est_budget_eur_max, est_trl,
                       suggested_consortium_type, alignment_score,
                       distinctiveness_score, generated_at, generator_version
                  FROM call_idea_suggestions
                 WHERE call_id = $1
                   AND generated_at >= now() - ($2 || ' days')::interval
                 ORDER BY suggestion_index
                """,
                call_id,
                str(CACHE_TTL_DAYS),
            )
        if not rows:
            return None
        ideas = [
            GeneratedIdea(
                title=str(r["title"]),
                abstract=str(r["abstract"]),
                technology_angle=str(r["technology_angle"] or ""),
                impact_thesis=str(r["impact_thesis"] or ""),
                est_budget_eur_min=_to_float(r["est_budget_eur_min"]),
                est_budget_eur_max=_to_float(r["est_budget_eur_max"]),
                est_trl=r["est_trl"],
                suggested_consortium_type=str(
                    r["suggested_consortium_type"] or ""
                ),
                alignment_score=_to_float(r["alignment_score"]) or 0.0,
                distinctiveness_score=_to_float(r["distinctiveness_score"]),
            )
            for r in rows
        ]
        return IdeaGenerationResult(
            call_id=call_id,
            ideas=ideas,
            generated_at=rows[0]["generated_at"].isoformat(),
            generator_version=str(rows[0]["generator_version"] or GENERATOR_VERSION),
            from_cache=True,
        )

    async def _persist_cache(
        self, call_id: UUID, ideas: list[GeneratedIdea]
    ) -> None:
        """Replace the call's cached slate. Delete-then-insert because
        the suggestion_index set may shrink between regenerations."""

        rows = [
            (
                call_id,
                index,
                idea.title,
                idea.abstract,
                idea.technology_angle,
                idea.impact_thesis,
                idea.est_budget_eur_min,
                idea.est_budget_eur_max,
                idea.est_trl,
                idea.suggested_consortium_type,
                idea.alignment_score,
                idea.distinctiveness_score,
                GENERATOR_VERSION,
            )
            for index, idea in enumerate(ideas)
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM call_idea_suggestions WHERE call_id = $1", call_id
            )
            await conn.executemany(
                """
                INSERT INTO call_idea_suggestions (
                  call_id, suggestion_index, title, abstract,
                  technology_angle, impact_thesis, est_budget_eur_min,
                  est_budget_eur_max, est_trl, suggested_consortium_type,
                  alignment_score, distinctiveness_score, generator_version,
                  generated_at
                ) VALUES (
                  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, now()
                )
                """,
                rows,
            )

    # ── Context loaders ──────────────────────────────────────────────

    async def _load_call(self, call_id: UUID) -> _CallContext:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, programme_id, agency_id, title, scope_summary,
                       call_text, topic_keywords, sectors, trl_min, trl_max,
                       budget_per_project_min_eur, budget_per_project_max_eur,
                       eligibility_tags, language
                  FROM calls
                 WHERE id = $1
                """,
                call_id,
            )
        if row is None:
            raise ValueError(f"call {call_id} not found")
        return _CallContext(
            call_id=UUID(str(row["id"])),
            programme_id=str(row["programme_id"]),
            agency_id=row["agency_id"],
            title=str(row["title"]),
            scope_summary=row["scope_summary"],
            call_text=row["call_text"],
            topic_keywords=list(row["topic_keywords"] or []),
            sectors=list(row["sectors"] or []),
            trl_min=row["trl_min"],
            trl_max=row["trl_max"],
            budget_min_eur=_to_float(row["budget_per_project_min_eur"]),
            budget_max_eur=_to_float(row["budget_per_project_max_eur"]),
            eligibility_tags=list(row["eligibility_tags"] or []),
            language=str(row["language"]),
        )

    async def _load_org_priors(self, tenant_id: UUID) -> _OrgPriors | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT entity_type, country, sectors, technology_areas,
                       trl_current, expertise_keywords
                  FROM organization_profiles
                 WHERE tenant_id = $1
                """,
                tenant_id,
            )
        if row is None:
            return None
        return _OrgPriors(
            entity_type=row["entity_type"],
            country=row["country"],
            sectors=list(row["sectors"] or []),
            technology_areas=list(row["technology_areas"] or []),
            trl_current=row["trl_current"],
            expertise_keywords=list(row["expertise_keywords"] or []),
        )

    # ── LLM ──────────────────────────────────────────────────────────

    async def _llm_generate(
        self,
        call: _CallContext,
        org: _OrgPriors | None,
        n_ideas: int,
    ) -> list[GeneratedIdea]:
        user_msg = _build_prompt(call, org, n_ideas)
        request = LLMRequest(
            task="idea_generator",
            tenant_id=self._tenant_id,
            system=_SYSTEM,
            messages=[LLMMessage(role="user", content=user_msg)],
            temperature=0.6,
            max_tokens=6144,
        )
        try:
            response = await self._router.complete(request)
            payload = _parse_response(response.text)
        except Exception:
            logger.exception(
                "idea_generator_llm_failed",
                extra={"call_id": str(call.call_id)},
            )
            return []

        ideas: list[GeneratedIdea] = []
        for raw in payload.get("ideas", [])[:n_ideas]:
            idea = _coerce_idea(raw)
            if idea is not None:
                ideas.append(idea)
        return ideas


# ── Prompt + parser ──────────────────────────────────────────────────────


def _build_prompt(
    call: _CallContext, org: _OrgPriors | None, n_ideas: int
) -> str:
    payload: dict[str, Any] = {
        "n_ideas_requested": n_ideas,
        "call": {
            "programme_id": call.programme_id,
            "agency_id": call.agency_id,
            "title": call.title,
            "scope_summary": call.scope_summary,
            "call_text_excerpt": (call.call_text or "")[:4000],
            "topic_keywords": call.topic_keywords,
            "sectors": call.sectors,
            "trl_band": [call.trl_min, call.trl_max],
            "budget_band_eur": [call.budget_min_eur, call.budget_max_eur],
            "eligibility_tags": call.eligibility_tags,
            "language": call.language,
        },
    }
    if org is not None:
        payload["organization"] = {
            "entity_type": org.entity_type,
            "country": org.country,
            "sectors": org.sectors,
            "technology_areas": org.technology_areas,
            "trl_current": org.trl_current,
            "expertise_keywords": org.expertise_keywords,
        }
    return json.dumps(payload, ensure_ascii=False)


def _parse_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "idea_generator_invalid_json", extra={"text": text[:300]}
        )
        return {}
    return result if isinstance(result, dict) else {}


def _coerce_idea(raw: Any) -> GeneratedIdea | None:
    """Validate + coerce one LLM idea dict. Drops malformed entries
    rather than failing the whole slate."""

    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title", "")).strip()
    abstract = str(raw.get("abstract", "")).strip()
    if not title or not abstract:
        return None
    alignment = raw.get("alignment_score")
    try:
        alignment_score = float(alignment) if alignment is not None else 0.5
    except (TypeError, ValueError):
        alignment_score = 0.5
    alignment_score = max(0.0, min(1.0, alignment_score))

    est_trl = raw.get("est_trl")
    if isinstance(est_trl, int) and not (1 <= est_trl <= 9):
        est_trl = None

    return GeneratedIdea(
        title=title[:300],
        abstract=abstract[:8000],
        technology_angle=str(raw.get("technology_angle", "")).strip()[:2000],
        impact_thesis=str(raw.get("impact_thesis", "")).strip()[:2000],
        est_budget_eur_min=_to_float(raw.get("est_budget_eur_min")),
        est_budget_eur_max=_to_float(raw.get("est_budget_eur_max")),
        est_trl=est_trl if isinstance(est_trl, int) else None,
        suggested_consortium_type=str(
            raw.get("suggested_consortium_type", "")
        ).strip()[:500],
        alignment_score=alignment_score,
        distinctiveness_score=None,  # V2: DistinctivenessScorer pass
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "GENERATOR_VERSION",
    "GeneratedIdea",
    "IdeaGenerationResult",
    "IdeaGenerator",
]
