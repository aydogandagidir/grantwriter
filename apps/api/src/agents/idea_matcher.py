"""IdeaMatcher — match one project idea against the open-call catalogue.

Four-layer hybrid scorer per the Faz 2 plan:

  1. **Hard filter (SQL, ~50ms)** — status, deadline window, eligibility
     tag overlap with the org profile's entity_type, TRL band overlap,
     budget range overlap. Cuts ~150 open calls down to 30-80.

  2. **Semantic match (pgvector HNSW, ~100ms)** — cosine similarity
     between the idea embedding and ``calls.embedding`` (cast to
     halfvec(3072) so the index is actually used). Top 20.

  3. **Soft scoring (Python, ~20ms)** — keyword Jaccard overlap, sector
     overlap, TRL fit gradient (how well does the idea TRL sit inside
     the call band), budget fit gradient. Combined score:

         total = 0.50*semantic + 0.20*keyword + 0.15*sector
                  + 0.10*trl_fit + 0.05*budget_fit

  4. **LLM re-rank (Sonnet, ~3-5s)** — top-10 candidates + idea + org
     profile fed to ``idea_matcher`` task; model returns ordered list
     plus a per-match rationale and identified gaps (what the idea
     would need to add to fit the call).

Results land in ``idea_call_matches`` with a 24h TTL semantics (the
``computed_at`` column lets the caller decide whether to re-run; this
class always upserts).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg

from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter
from src.rag.base import Embedder

logger = logging.getLogger(__name__)


DEFAULT_TOP_K = 5
DEFAULT_HARD_FILTER_LIMIT = 80
DEFAULT_SEMANTIC_POOL = 20
DEFAULT_RERANK_POOL = 10
MODEL_VERSION = "idea_matcher-v1"


# ── Result dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CallMatchResult:
    """One ranked call in the match list."""

    call_id: UUID
    total_score: float
    semantic_score: float
    keyword_overlap_score: float
    sector_score: float
    trl_fit_score: float
    budget_fit_score: float
    rationale_tr: str
    rationale_en: str
    identified_gaps: list[str]


@dataclass(frozen=True)
class FilterStats:
    """Funnel observability for the four layers."""

    hard_filter_pool: int
    semantic_pool: int
    reranked: int


@dataclass(frozen=True)
class IdeaMatchResult:
    idea_id: UUID
    matches: list[CallMatchResult]
    filter_stats: FilterStats
    computed_at: str
    model_version: str = MODEL_VERSION
    persisted: bool = False


@dataclass(frozen=True)
class _IdeaContext:
    """Cached snapshot of the idea + its tenant's org profile."""

    idea_id: UUID
    tenant_id: UUID
    title: str
    abstract: str
    embedding: list[float] | None
    sectors: list[str]
    keywords: list[str]
    trl_estimate: int | None
    budget_min_eur: float | None
    budget_max_eur: float | None
    org_country: str | None
    org_entity_type: str | None
    org_trl_current: int | None
    org_languages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _CandidateCall:
    """One row from the hard-filter SQL, enriched with semantic score."""

    call_id: UUID
    programme_id: str
    agency_id: str | None
    title: str
    deadline_iso: str | None
    topic_keywords: list[str]
    sectors: list[str]
    trl_min: int | None
    trl_max: int | None
    budget_min_eur: float | None
    budget_max_eur: float | None
    embedding: list[float] | None
    scope_summary: str | None
    eligibility_tags: list[str]
    semantic_score: float = 0.0


# ── Scoring weights (overridable for A/B) ────────────────────────────────

WEIGHT_SEMANTIC: float = 0.50
WEIGHT_KEYWORD: float = 0.20
WEIGHT_SECTOR: float = 0.15
WEIGHT_TRL_FIT: float = 0.10
WEIGHT_BUDGET_FIT: float = 0.05


# ── LLM prompts ──────────────────────────────────────────────────────────

_RERANK_SYSTEM = """\
You rank candidate grant calls for a project idea, in best-to-worst order.

For each candidate, also produce a SHORT bilingual rationale (Turkish + English,
≤ 240 chars each, plain prose, NO markdown) and 1-3 ``identified_gaps`` —
concrete things the project would have to add or change to be competitive
under this specific call (e.g. "Konsorsiyum lideri olarak Türk KOBİ gerekli",
"TRL 4 → TRL 6 olgunluk ispatı sunulmalı").

Return ONLY a single JSON object, no markdown, no commentary:

{
  "ranked": [
    {
      "call_id": "<uuid>",
      "rationale_tr": "...",
      "rationale_en": "...",
      "identified_gaps": ["...", "..."]
    },
    ...
  ]
}

Use only call_ids from the input. Do not invent calls. If you cannot judge
a candidate fairly (insufficient information), put it last and say so in
its rationale.
"""


# ── Matcher ──────────────────────────────────────────────────────────────


class IdeaMatcher:
    """End-to-end matcher for one idea_id → ranked call list."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        embedder: Embedder,
        router: LLMRouter,
        tenant_id: UUID,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._router = router
        self._tenant_id = tenant_id

    async def match(
        self,
        idea_id: UUID,
        *,
        top_k: int = DEFAULT_TOP_K,
        hard_limit: int = DEFAULT_HARD_FILTER_LIMIT,
        semantic_pool: int = DEFAULT_SEMANTIC_POOL,
        rerank_pool: int = DEFAULT_RERANK_POOL,
        persist: bool = True,
    ) -> IdeaMatchResult:
        """Run the four-layer pipeline. Persists results unless
        ``persist=False`` (test mode)."""

        idea_ctx = await self._load_idea_context(idea_id)
        hard_candidates = await self._hard_filter(idea_ctx, limit=hard_limit)
        if not hard_candidates:
            return _empty_result(idea_id, hard_pool=0)

        scored = await self._semantic_score(
            idea_ctx, hard_candidates, pool_size=semantic_pool
        )
        if not scored:
            return _empty_result(idea_id, hard_pool=len(hard_candidates))

        soft_scored = _soft_score(idea_ctx, scored)
        # Sort by total + keep top ``rerank_pool`` for LLM, then trim to top_k.
        soft_scored.sort(key=lambda x: x["total_score"], reverse=True)
        rerank_input = soft_scored[:rerank_pool]

        ranked = await self._llm_rerank(idea_ctx, rerank_input)
        # Trim to top_k after re-rank (re-rank may shuffle order, doesn't
        # add new items).
        final = ranked[:top_k]

        match_results = [_build_match_result(item) for item in final]

        if persist:
            await self._persist_matches(idea_id, match_results)

        return IdeaMatchResult(
            idea_id=idea_id,
            matches=match_results,
            filter_stats=FilterStats(
                hard_filter_pool=len(hard_candidates),
                semantic_pool=len(scored),
                reranked=len(rerank_input),
            ),
            computed_at=_now_iso(),
            persisted=persist,
        )

    # ── Layer 1: idea + org context ─────────────────────────────────

    async def _load_idea_context(self, idea_id: UUID) -> _IdeaContext:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT i.id, i.tenant_id, i.title, i.abstract, i.embedding,
                       i.sectors, i.keywords, i.trl_estimate,
                       i.budget_estimate_eur_min, i.budget_estimate_eur_max,
                       op.country, op.entity_type, op.trl_current,
                       op.preferred_languages
                  FROM project_ideas i
                  LEFT JOIN organization_profiles op ON op.tenant_id = i.tenant_id
                 WHERE i.id = $1
                """,
                idea_id,
            )
        if row is None:
            raise ValueError(f"idea {idea_id} not found")
        return _IdeaContext(
            idea_id=UUID(str(row["id"])),
            tenant_id=UUID(str(row["tenant_id"])),
            title=str(row["title"]),
            abstract=str(row["abstract"]),
            embedding=_vector_to_floats(row["embedding"]),
            sectors=list(row["sectors"] or []),
            keywords=list(row["keywords"] or []),
            trl_estimate=row["trl_estimate"],
            budget_min_eur=_to_float(row["budget_estimate_eur_min"]),
            budget_max_eur=_to_float(row["budget_estimate_eur_max"]),
            org_country=row["country"],
            org_entity_type=row["entity_type"],
            org_trl_current=row["trl_current"],
            org_languages=list(row["preferred_languages"] or []),
        )

    # ── Layer 1: hard filter ────────────────────────────────────────

    async def _hard_filter(
        self, ctx: _IdeaContext, *, limit: int
    ) -> list[_CandidateCall]:
        """SQL pre-filter: status, deadline window, eligibility / TRL /
        budget overlap. Returns rows still in scope for semantic ranking."""

        # Build dynamic WHERE clauses. Org-profile fields are optional —
        # missing values just skip their respective filter.
        clauses: list[str] = ["status IN ('open', 'closing_soon')"]
        params: list[Any] = []

        # Deadline: at least 14 days from now (matches the Faz 2 plan).
        clauses.append(
            "(deadline IS NULL OR deadline >= (now()::date + interval '14 days')::date)"
        )

        if ctx.org_entity_type:
            # Either no eligibility_tags published, or the org's type is in it.
            clauses.append(
                f"(cardinality(eligibility_tags) = 0 OR eligibility_tags && ARRAY[${len(params) + 1}]::text[])"
            )
            params.append(ctx.org_entity_type)

        if ctx.org_country:
            clauses.append(
                f"""
                (
                  cardinality(geo_scope) = 0
                  OR geo_scope && ARRAY[${len(params) + 1}]::text[]
                  OR 'eu27' = ANY(geo_scope)
                  OR 'assoc' = ANY(geo_scope)
                  OR 'global' = ANY(geo_scope)
                )
                """
            )
            params.append(ctx.org_country.lower())

        if ctx.trl_estimate is not None:
            clauses.append(
                f"(trl_max IS NULL OR trl_max >= ${len(params) + 1})"
            )
            params.append(ctx.trl_estimate)
            clauses.append(
                f"(trl_min IS NULL OR trl_min <= ${len(params) + 1})"
            )
            params.append(ctx.trl_estimate)

        if ctx.budget_max_eur is not None:
            clauses.append(
                f"(budget_per_project_min_eur IS NULL OR budget_per_project_min_eur <= ${len(params) + 1})"
            )
            params.append(ctx.budget_max_eur)
        if ctx.budget_min_eur is not None:
            clauses.append(
                f"(budget_per_project_max_eur IS NULL OR budget_per_project_max_eur >= ${len(params) + 1})"
            )
            params.append(ctx.budget_min_eur)

        where_sql = " AND ".join(clauses)
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, programme_id, agency_id, title, deadline,
                       topic_keywords, sectors, trl_min, trl_max,
                       budget_per_project_min_eur, budget_per_project_max_eur,
                       embedding, scope_summary, eligibility_tags
                  FROM calls
                 WHERE {where_sql}
                 ORDER BY deadline ASC NULLS LAST
                 LIMIT ${len(params)}
                """,
                *params,
            )

        return [
            _CandidateCall(
                call_id=UUID(str(r["id"])),
                programme_id=str(r["programme_id"]),
                agency_id=r["agency_id"],
                title=str(r["title"]),
                deadline_iso=r["deadline"].isoformat() if r["deadline"] else None,
                topic_keywords=list(r["topic_keywords"] or []),
                sectors=list(r["sectors"] or []),
                trl_min=r["trl_min"],
                trl_max=r["trl_max"],
                budget_min_eur=_to_float(r["budget_per_project_min_eur"]),
                budget_max_eur=_to_float(r["budget_per_project_max_eur"]),
                embedding=_vector_to_floats(r["embedding"]),
                scope_summary=r["scope_summary"],
                eligibility_tags=list(r["eligibility_tags"] or []),
            )
            for r in rows
        ]

    # ── Layer 2: semantic match ─────────────────────────────────────

    async def _semantic_score(
        self,
        ctx: _IdeaContext,
        candidates: list[_CandidateCall],
        *,
        pool_size: int,
    ) -> list[_CandidateCall]:
        """Sort candidates by cosine similarity against the idea
        embedding. Falls back to the same order if the idea or candidate
        embeddings are missing (worst case: relevance score = 0)."""

        idea_vec = ctx.embedding
        if idea_vec is None:
            # Materialise the idea's embedding now if the row was
            # created without one. The result is cached for free in
            # the returned context but we don't persist back to DB —
            # that's the caller's job (POST /ideas computes and stores).
            idea_vec = await self._embedder.embed(_idea_text(ctx))

        # Compute cosine for each candidate; candidates without an
        # embedding score 0 — they fall to the end of the soft-score
        # ladder unless they have very strong keyword/sector overlap.
        scored: list[_CandidateCall] = []
        for cand in candidates:
            cos = _cosine(idea_vec, cand.embedding) if cand.embedding else 0.0
            scored.append(
                _CandidateCall(
                    call_id=cand.call_id,
                    programme_id=cand.programme_id,
                    agency_id=cand.agency_id,
                    title=cand.title,
                    deadline_iso=cand.deadline_iso,
                    topic_keywords=cand.topic_keywords,
                    sectors=cand.sectors,
                    trl_min=cand.trl_min,
                    trl_max=cand.trl_max,
                    budget_min_eur=cand.budget_min_eur,
                    budget_max_eur=cand.budget_max_eur,
                    embedding=cand.embedding,
                    scope_summary=cand.scope_summary,
                    eligibility_tags=cand.eligibility_tags,
                    semantic_score=cos,
                )
            )
        scored.sort(key=lambda c: c.semantic_score, reverse=True)
        return scored[:pool_size]

    # ── Layer 4: LLM re-rank ────────────────────────────────────────

    async def _llm_rerank(
        self,
        ctx: _IdeaContext,
        scored_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ask the LLM to re-rank top candidates + produce rationales."""

        if not scored_items:
            return []

        user_msg = _build_rerank_prompt(ctx, scored_items)
        request = LLMRequest(
            task="idea_matcher",
            tenant_id=self._tenant_id,
            system=_RERANK_SYSTEM,
            messages=[LLMMessage(role="user", content=user_msg)],
            temperature=0.1,
            max_tokens=4096,
        )

        try:
            response = await self._router.complete(request)
            payload = _parse_rerank_response(response.text)
        except Exception:
            logger.exception(
                "idea_matcher_rerank_failed",
                extra={"idea_id": str(ctx.idea_id)},
            )
            payload = {}

        by_id = {item["call_id"]: item for item in scored_items}
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in payload.get("ranked", []):
            call_id = str(entry.get("call_id", ""))
            if call_id not in by_id or call_id in seen:
                continue
            seen.add(call_id)
            merged.append(
                {
                    **by_id[call_id],
                    "rationale_tr": str(entry.get("rationale_tr", "")).strip()[:1000],
                    "rationale_en": str(entry.get("rationale_en", "")).strip()[:1000],
                    "identified_gaps": [
                        str(g)[:300]
                        for g in (entry.get("identified_gaps") or [])
                        if isinstance(g, str) and g.strip()
                    ][:5],
                }
            )

        # Append any non-re-ranked items at the bottom (defensive — keeps
        # the top_k slate full when the LLM drops items by mistake).
        for item in scored_items:
            if item["call_id"] in seen:
                continue
            merged.append(
                {**item, "rationale_tr": "", "rationale_en": "", "identified_gaps": []}
            )
        return merged

    # ── Persist ─────────────────────────────────────────────────────

    async def _persist_matches(
        self, idea_id: UUID, matches: list[CallMatchResult]
    ) -> None:
        """Upsert into idea_call_matches; composite PK gives us
        idempotent re-rerank without delete-then-insert."""

        if not matches:
            return
        rows = [
            (
                idea_id,
                m.call_id,
                m.total_score,
                m.semantic_score,
                m.keyword_overlap_score,
                m.sector_score,
                m.trl_fit_score,
                m.budget_fit_score,
                m.rationale_tr,
                m.rationale_en,
                list(m.identified_gaps),
                MODEL_VERSION,
            )
            for m in matches
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO idea_call_matches (
                  idea_id, call_id, total_score, semantic_score,
                  keyword_overlap_score, sector_score, trl_fit_score,
                  budget_fit_score, rationale_tr, rationale_en,
                  identified_gaps, model_version, computed_at
                ) VALUES (
                  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text[],
                  $12, now()
                )
                ON CONFLICT (idea_id, call_id) DO UPDATE SET
                  total_score = EXCLUDED.total_score,
                  semantic_score = EXCLUDED.semantic_score,
                  keyword_overlap_score = EXCLUDED.keyword_overlap_score,
                  sector_score = EXCLUDED.sector_score,
                  trl_fit_score = EXCLUDED.trl_fit_score,
                  budget_fit_score = EXCLUDED.budget_fit_score,
                  rationale_tr = EXCLUDED.rationale_tr,
                  rationale_en = EXCLUDED.rationale_en,
                  identified_gaps = EXCLUDED.identified_gaps,
                  model_version = EXCLUDED.model_version,
                  computed_at = now()
                """,
                rows,
            )


# ── Soft-scoring helpers ─────────────────────────────────────────────────


def _soft_score(
    ctx: _IdeaContext, candidates: list[_CandidateCall]
) -> list[dict[str, Any]]:
    """Compute keyword/sector/TRL/budget components and combine into
    one total_score per candidate. Returns flat dicts for easy
    serialisation into the LLM prompt."""

    idea_keywords = _norm_set(ctx.keywords)
    idea_sectors = _norm_set(ctx.sectors)
    out: list[dict[str, Any]] = []
    for cand in candidates:
        keyword = _jaccard(idea_keywords, _norm_set(cand.topic_keywords))
        sector = _jaccard(idea_sectors, _norm_set(cand.sectors))
        trl_fit = _trl_fit(ctx.trl_estimate, cand.trl_min, cand.trl_max)
        budget_fit = _budget_fit(
            ctx.budget_min_eur,
            ctx.budget_max_eur,
            cand.budget_min_eur,
            cand.budget_max_eur,
        )
        total = (
            WEIGHT_SEMANTIC * cand.semantic_score
            + WEIGHT_KEYWORD * keyword
            + WEIGHT_SECTOR * sector
            + WEIGHT_TRL_FIT * trl_fit
            + WEIGHT_BUDGET_FIT * budget_fit
        )
        out.append(
            {
                "call_id": str(cand.call_id),
                "programme_id": cand.programme_id,
                "agency_id": cand.agency_id,
                "title": cand.title,
                "scope_summary": cand.scope_summary,
                "deadline_iso": cand.deadline_iso,
                "topic_keywords": cand.topic_keywords,
                "sectors": cand.sectors,
                "trl_min": cand.trl_min,
                "trl_max": cand.trl_max,
                "budget_min_eur": cand.budget_min_eur,
                "budget_max_eur": cand.budget_max_eur,
                "eligibility_tags": cand.eligibility_tags,
                "semantic_score": round(cand.semantic_score, 4),
                "keyword_overlap_score": round(keyword, 4),
                "sector_score": round(sector, 4),
                "trl_fit_score": round(trl_fit, 4),
                "budget_fit_score": round(budget_fit, 4),
                "total_score": round(total, 4),
            }
        )
    return out


def _norm_set(items: list[str]) -> set[str]:
    return {item.strip().lower() for item in items if item and item.strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _trl_fit(
    idea_trl: int | None, call_trl_min: int | None, call_trl_max: int | None
) -> float:
    """1.0 if idea sits inside the call band, decays linearly outside."""

    if idea_trl is None or call_trl_min is None or call_trl_max is None:
        return 0.5  # neutral when unknown
    if call_trl_min <= idea_trl <= call_trl_max:
        return 1.0
    delta = min(abs(idea_trl - call_trl_min), abs(idea_trl - call_trl_max))
    return max(0.0, 1.0 - 0.25 * delta)


def _budget_fit(
    idea_min: float | None,
    idea_max: float | None,
    call_min: float | None,
    call_max: float | None,
) -> float:
    """Fraction of the user's budget band that overlaps the call's
    advertised band. 1.0 on full overlap; 0.0 on no overlap."""

    if (
        idea_min is None
        or idea_max is None
        or call_min is None
        or call_max is None
        or idea_max < idea_min
        or call_max < call_min
    ):
        return 0.5
    overlap_lo = max(idea_min, call_min)
    overlap_hi = min(idea_max, call_max)
    if overlap_hi < overlap_lo:
        return 0.0
    idea_span = max(1.0, idea_max - idea_min)
    return min(1.0, (overlap_hi - overlap_lo) / idea_span)


# ── Vector helpers ───────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot: float = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a: float = sum(x * x for x in a) ** 0.5
    norm_b: float = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _vector_to_floats(raw: Any) -> list[float] | None:
    """Coerce pgvector's typed return into a plain list[float]. The
    ``register_vector`` codec already gives us list[float] in production;
    older pools return strings like ``"[0.1,0.2,...]"``."""

    if raw is None:
        return None
    if isinstance(raw, list):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        try:
            return [float(x) for x in raw.strip("[]").split(",") if x.strip()]
        except ValueError:
            return None
    return None


# ── Prompt + parser ──────────────────────────────────────────────────────


def _idea_text(ctx: _IdeaContext) -> str:
    return f"{ctx.title}\n\n{ctx.abstract}"


def _build_rerank_prompt(ctx: _IdeaContext, items: list[dict[str, Any]]) -> str:
    candidates = [
        {
            "call_id": item["call_id"],
            "programme_id": item["programme_id"],
            "agency_id": item["agency_id"],
            "title": item["title"],
            "scope_summary": (item.get("scope_summary") or "")[:600],
            "deadline_iso": item.get("deadline_iso"),
            "topic_keywords": item.get("topic_keywords", []),
            "sectors": item.get("sectors", []),
            "trl_band": [item.get("trl_min"), item.get("trl_max")],
            "budget_band_eur": [
                item.get("budget_min_eur"),
                item.get("budget_max_eur"),
            ],
            "eligibility_tags": item.get("eligibility_tags", []),
            "soft_scores": {
                "semantic": item["semantic_score"],
                "keyword": item["keyword_overlap_score"],
                "sector": item["sector_score"],
                "trl_fit": item["trl_fit_score"],
                "budget_fit": item["budget_fit_score"],
                "total": item["total_score"],
            },
        }
        for item in items
    ]
    payload = {
        "idea": {
            "title": ctx.title,
            "abstract": ctx.abstract[:2000],
            "sectors": ctx.sectors,
            "keywords": ctx.keywords,
            "trl_estimate": ctx.trl_estimate,
            "budget_band_eur": [ctx.budget_min_eur, ctx.budget_max_eur],
        },
        "organization": {
            "country": ctx.org_country,
            "entity_type": ctx.org_entity_type,
            "trl_current": ctx.org_trl_current,
            "preferred_languages": ctx.org_languages,
        },
        "candidates": candidates,
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_rerank_response(text: str) -> dict[str, Any]:
    """Tolerant JSON extractor — strips fences, trims, defaults to empty."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove leading and trailing fences if present.
        cleaned = cleaned.strip("`")
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("idea_matcher_invalid_json", extra={"text": text[:300]})
        return {}
    if not isinstance(result, dict):
        return {}
    return result


# ── Result construction ─────────────────────────────────────────────────


def _build_match_result(item: dict[str, Any]) -> CallMatchResult:
    return CallMatchResult(
        call_id=UUID(item["call_id"]),
        total_score=float(item["total_score"]),
        semantic_score=float(item["semantic_score"]),
        keyword_overlap_score=float(item["keyword_overlap_score"]),
        sector_score=float(item["sector_score"]),
        trl_fit_score=float(item["trl_fit_score"]),
        budget_fit_score=float(item["budget_fit_score"]),
        rationale_tr=str(item.get("rationale_tr", "")),
        rationale_en=str(item.get("rationale_en", "")),
        identified_gaps=list(item.get("identified_gaps", [])),
    )


def _empty_result(idea_id: UUID, *, hard_pool: int) -> IdeaMatchResult:
    return IdeaMatchResult(
        idea_id=idea_id,
        matches=[],
        filter_stats=FilterStats(
            hard_filter_pool=hard_pool,
            semantic_pool=0,
            reranked=0,
        ),
        computed_at=_now_iso(),
        persisted=False,
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = [
    "CallMatchResult",
    "FilterStats",
    "IdeaMatchResult",
    "IdeaMatcher",
    "MODEL_VERSION",
]
