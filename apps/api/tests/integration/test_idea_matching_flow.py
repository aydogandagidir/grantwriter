"""Integration tests for the Faz 2 bidirectional-matching flow.

Drives the three agents (IdeaMatcher, IdeaGenerator, EligibilityChecker)
and their HTTP endpoints against a real database. Gated on
``live_db_pool`` — skipped when ``TEST_DATABASE_URL`` is unset, the
same gate the rest of the integration suite uses.

LLM calls are stubbed with a deterministic mock router (the agents only
touch ``router.complete``); embeddings use ``DeterministicEmbedder`` so
"find your own content back" works without OpenAI. That keeps every
assertion reproducible — no network, no nondeterminism.

Each test seeds its own tenant + calls with fresh UUIDs and tears them
down in a finally block, so the suite is order-independent and safe to
re-run against a persistent test DB.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.agents.eligibility_checker import EligibilityChecker
from src.agents.idea_generator import IdeaGenerator
from src.agents.idea_matcher import IdeaMatcher
from src.core.auth import get_current_user_id
from src.core.db import get_db
from src.core.embedder_dep import get_embedder
from src.core.llm_dep import get_llm_router
from src.llm.base import LLMRequest, LLMResponse, LLMUsage
from src.main import create_app
from src.rag.corpus_manager import _vector_literal
from src.rag.embedder import DeterministicEmbedder

# Shared deterministic embedder — same instance across a test so an
# idea and a call seeded from related text land near each other.
_EMBEDDER = DeterministicEmbedder(seed_namespace="faz2-integration")


# ── Mock LLM router ──────────────────────────────────────────────────────


class _MockRouter:
    """Duck-typed LLMRouter. The agents only call ``complete``; we echo
    back deterministic JSON shaped per ``request.task`` so the matcher /
    generator parsers have something real to chew on."""

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(
            text=self._response_for(request),
            model="mock-model",
            provider="claude",
            usage=LLMUsage(input_tokens=10, output_tokens=20),
            finish_reason="stop",
            cost_usd=0.0,
            used_byok=False,
        )

    @staticmethod
    def _response_for(request: LLMRequest) -> str:
        payload = json.loads(request.messages[0].content)
        if request.task == "idea_matcher":
            # Re-rank: keep the soft-score order, attach a rationale +
            # one gap per candidate.
            candidates = payload.get("candidates", [])
            return json.dumps(
                {
                    "ranked": [
                        {
                            "call_id": c["call_id"],
                            "rationale_tr": f"Gerekce {c['call_id'][:8]}",
                            "rationale_en": f"Rationale {c['call_id'][:8]}",
                            "identified_gaps": ["Strengthen the consortium"],
                        }
                        for c in candidates
                    ]
                }
            )
        if request.task == "idea_generator":
            n = payload.get("n_ideas_requested", 3)
            return json.dumps(
                {
                    "ideas": [
                        {
                            "title": f"Generated idea {i}",
                            "abstract": "Mock abstract. " * 20,
                            "technology_angle": "Edge inference",
                            "impact_thesis": "Cuts cost for SMEs",
                            "est_budget_eur_min": 500_000,
                            "est_budget_eur_max": 1_500_000,
                            "est_trl": 5,
                            "suggested_consortium_type": "SME lead + RTO",
                            "alignment_score": 0.8,
                        }
                        for i in range(n)
                    ]
                }
            )
        return "{}"


# ── Seed helpers ─────────────────────────────────────────────────────────


async def _seed_tenant_user(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    tenant_id, user_id = uuid4(), uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Faz2 Test Tenant",
            f"faz2-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"faz2-{user_id}@example.com",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) values ($1, $2, 'owner')",
            user_id,
            tenant_id,
        )
    return tenant_id, user_id


async def _seed_org_profile(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    entity_type: str = "sme",
    country: str = "tr",
    trl_current: int | None = 5,
    sectors: list[str] | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into organization_profiles
              (tenant_id, entity_type, country, trl_current, sectors)
            values ($1, $2, $3, $4, $5::text[])
            on conflict (tenant_id) do update set
              entity_type = excluded.entity_type,
              country = excluded.country,
              trl_current = excluded.trl_current,
              sectors = excluded.sectors
            """,
            tenant_id,
            entity_type,
            country,
            trl_current,
            sectors or ["software"],
        )


async def _seed_call(
    pool: asyncpg.Pool,
    *,
    title: str = "AI for industrial maintenance",
    programme_id: str = "tubitak_1501",
    status: str = "open",
    deadline: date | None = None,
    geo_scope: list[str] | None = None,
    eligibility_tags: list[str] | None = None,
    trl_min: int | None = 3,
    trl_max: int | None = 7,
    sectors: list[str] | None = None,
    topic_keywords: list[str] | None = None,
    budget_min: float | None = 500_000,
    budget_max: float | None = 2_000_000,
    partner_consortium_required: bool = False,
    scope_summary: str = "Industrial AI scope",
) -> UUID:
    call_id = uuid4()
    if deadline is None:
        deadline = date.today() + timedelta(days=90)
    vec = await _EMBEDDER.embed(f"{title} {scope_summary}")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into calls (
              id, programme_id, source, external_id, title, language,
              status, deadline, geo_scope, eligibility_tags, trl_min,
              trl_max, sectors, topic_keywords,
              budget_per_project_min_eur, budget_per_project_max_eur,
              scope_summary, call_text, partner_consortium_required,
              embedding
            ) values (
              $1, $2, 'manual', $3, $4, 'en',
              $5, $6, $7::text[], $8::text[], $9,
              $10, $11::text[], $12::text[],
              $13, $14, $15, $16, $17, $18::vector(3072)
            )
            """,
            call_id,
            programme_id,
            f"faz2-call-{call_id}",
            title,
            status,
            deadline,
            geo_scope or ["eu27", "assoc"],
            eligibility_tags or ["sme"],
            trl_min,
            trl_max,
            sectors or ["software"],
            topic_keywords or ["ai", "maintenance"],
            budget_min,
            budget_max,
            scope_summary,
            f"{title}. {scope_summary}. Full call text.",
            partner_consortium_required,
            _vector_literal(vec),
        )
    return call_id


async def _seed_idea(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    user_id: UUID,
    *,
    title: str = "Predictive maintenance with edge AI",
    abstract: str | None = None,
    sectors: list[str] | None = None,
    keywords: list[str] | None = None,
    trl: int | None = 5,
    budget_min: float | None = 800_000,
    budget_max: float | None = 1_500_000,
) -> UUID:
    idea_id = uuid4()
    abstract = abstract or ("Edge-AI predictive maintenance for factory PLCs. " * 8)
    vec = await _EMBEDDER.embed(f"{title} {abstract}")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into project_ideas (
              id, tenant_id, created_by, source, title, abstract,
              sectors, keywords, trl_estimate, budget_estimate_eur_min,
              budget_estimate_eur_max, embedding, status
            ) values (
              $1, $2, $3, 'user_input', $4, $5,
              $6::text[], $7::text[], $8, $9, $10,
              $11::vector(3072), 'active'
            )
            """,
            idea_id,
            tenant_id,
            user_id,
            title,
            abstract,
            sectors or ["software"],
            keywords or ["ai", "maintenance"],
            trl,
            budget_min,
            budget_max,
            _vector_literal(vec),
        )
    return idea_id


async def _cleanup(
    pool: asyncpg.Pool,
    *,
    tenant_ids: tuple[UUID, ...] = (),
    user_ids: tuple[UUID, ...] = (),
    call_ids: tuple[UUID, ...] = (),
) -> None:
    """Tear down in FK-safe order. Children before parents."""

    async with pool.acquire() as conn:
        if call_ids:
            await conn.execute(
                "delete from call_idea_suggestions where call_id = any($1::uuid[])",
                list(call_ids),
            )
            await conn.execute(
                "delete from idea_call_matches where call_id = any($1::uuid[])",
                list(call_ids),
            )
        if tenant_ids:
            await conn.execute(
                """
                delete from idea_call_matches where idea_id in (
                  select id from project_ideas where tenant_id = any($1::uuid[])
                )
                """,
                list(tenant_ids),
            )
            await conn.execute(
                "delete from project_ideas where tenant_id = any($1::uuid[])",
                list(tenant_ids),
            )
            await conn.execute(
                "delete from organization_profiles where tenant_id = any($1::uuid[])",
                list(tenant_ids),
            )
            await conn.execute(
                "delete from audit_log where tenant_id = any($1::uuid[])",
                list(tenant_ids),
            )
        if call_ids:
            await conn.execute(
                "delete from calls where id = any($1::uuid[])", list(call_ids)
            )
        if user_ids:
            await conn.execute(
                "delete from public.users where id = any($1::uuid[])", list(user_ids)
            )
            await conn.execute(
                "delete from auth.users where id = any($1::uuid[])", list(user_ids)
            )
        if tenant_ids:
            await conn.execute(
                "delete from tenants where id = any($1::uuid[])", list(tenant_ids)
            )


# ── App builder for endpoint tests ───────────────────────────────────────


def _build_app(
    *,
    pool: asyncpg.Pool,
    user_id: UUID,
    router: _MockRouter,
) -> FastAPI:
    app = create_app()
    # The matcher / generator routes read the app-wide pool off
    # app.state; the lifespan that normally sets it doesn't run under
    # ASGITransport, so wire it manually.
    app.state.db_pool = pool

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_embedder] = lambda: _EMBEDDER
    app.dependency_overrides[get_llm_router] = lambda: router
    return app


# ════════════════════════════════════════════════════════════════════════
# Agent-level integration
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_idea_matcher_full_pipeline(live_db_pool: asyncpg.Pool) -> None:
    """Seed calls + an idea, run all four matcher layers, assert the
    ranked output landed in idea_call_matches with a score breakdown."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(live_db_pool, tenant_id, country="tr", trl_current=5)
    call_a = await _seed_call(live_db_pool, title="Edge AI for factories")
    call_b = await _seed_call(live_db_pool, title="Renewable grid optimisation")
    idea_id = await _seed_idea(live_db_pool, tenant_id, user_id)
    router = _MockRouter()

    try:
        matcher = IdeaMatcher(
            pool=live_db_pool,
            embedder=_EMBEDDER,
            router=router,  # type: ignore[arg-type]  # duck-typed
            tenant_id=tenant_id,
        )
        result = await matcher.match(idea_id, top_k=5)

        assert result.idea_id == idea_id
        assert len(result.matches) >= 1
        assert result.filter_stats.hard_filter_pool >= 2  # both calls in scope
        # Re-rank touched the LLM exactly once.
        assert len(router.calls) == 1
        assert router.calls[0].task == "idea_matcher"

        # Every match carries a full score breakdown + bilingual rationale.
        for match in result.matches:
            assert 0.0 <= match.total_score <= 1.0
            assert match.rationale_tr and match.rationale_en
            assert match.identified_gaps

        # Persisted into idea_call_matches.
        async with live_db_pool.acquire() as conn:
            persisted = await conn.fetchval(
                "select count(*) from idea_call_matches where idea_id = $1",
                idea_id,
            )
        assert persisted == len(result.matches)
    finally:
        await _cleanup(
            live_db_pool,
            tenant_ids=(tenant_id,),
            user_ids=(user_id,),
            call_ids=(call_a, call_b),
        )


@pytest.mark.asyncio
async def test_idea_matcher_hard_filter_excludes_closed_call(
    live_db_pool: asyncpg.Pool,
) -> None:
    """A closed call must not survive layer 1, even if it's a perfect
    semantic match."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    open_call = await _seed_call(live_db_pool, title="Open AI maintenance call")
    closed_call = await _seed_call(
        live_db_pool, title="Open AI maintenance call", status="closed"
    )
    idea_id = await _seed_idea(live_db_pool, tenant_id, user_id)
    router = _MockRouter()

    try:
        matcher = IdeaMatcher(
            pool=live_db_pool,
            embedder=_EMBEDDER,
            router=router,  # type: ignore[arg-type]
            tenant_id=tenant_id,
        )
        result = await matcher.match(idea_id, top_k=5)
        matched_ids = {m.call_id for m in result.matches}
        assert open_call in matched_ids
        assert closed_call not in matched_ids
    finally:
        await _cleanup(
            live_db_pool,
            tenant_ids=(tenant_id,),
            user_ids=(user_id,),
            call_ids=(open_call, closed_call),
        )


@pytest.mark.asyncio
async def test_idea_matcher_empty_when_no_open_calls(
    live_db_pool: asyncpg.Pool,
) -> None:
    """No calls in scope → empty result, no LLM call, nothing persisted."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    # Only a closed call exists — hard filter rejects it.
    closed = await _seed_call(live_db_pool, status="closed")
    idea_id = await _seed_idea(live_db_pool, tenant_id, user_id)
    router = _MockRouter()

    try:
        matcher = IdeaMatcher(
            pool=live_db_pool,
            embedder=_EMBEDDER,
            router=router,  # type: ignore[arg-type]
            tenant_id=tenant_id,
        )
        result = await matcher.match(idea_id, top_k=5)
        assert result.matches == []
        assert result.filter_stats.hard_filter_pool == 0
        # No candidates → re-rank never runs.
        assert router.calls == []
    finally:
        await _cleanup(
            live_db_pool,
            tenant_ids=(tenant_id,),
            user_ids=(user_id,),
            call_ids=(closed,),
        )


@pytest.mark.asyncio
async def test_idea_generator_persists_and_serves_from_cache(
    live_db_pool: asyncpg.Pool,
) -> None:
    """First generate() hits the LLM + writes call_idea_suggestions;
    the second serves from cache with no LLM call."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    call_id = await _seed_call(live_db_pool, title="Quantum sensing call")
    router = _MockRouter()

    try:
        generator = IdeaGenerator(
            pool=live_db_pool,
            router=router,  # type: ignore[arg-type]
            tenant_id=tenant_id,
        )
        first = await generator.generate(call_id, n_ideas=3)
        assert first.from_cache is False
        assert len(first.ideas) == 3
        assert len(router.calls) == 1

        async with live_db_pool.acquire() as conn:
            cached_rows = await conn.fetchval(
                "select count(*) from call_idea_suggestions where call_id = $1",
                call_id,
            )
        assert cached_rows == 3

        second = await generator.generate(call_id, n_ideas=3)
        assert second.from_cache is True
        assert len(second.ideas) == 3
        # No second LLM call — served from the cache.
        assert len(router.calls) == 1
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_idea_generator_profile_bias_skips_cache(
    live_db_pool: asyncpg.Pool,
) -> None:
    """A profile-biased request must regenerate even when a generic
    cached slate exists — and must not poison the shared cache."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(live_db_pool, tenant_id, sectors=["aerospace"])
    call_id = await _seed_call(live_db_pool)
    router = _MockRouter()

    try:
        generator = IdeaGenerator(
            pool=live_db_pool,
            router=router,  # type: ignore[arg-type]
            tenant_id=tenant_id,
        )
        # Seed the generic cache first.
        await generator.generate(call_id, n_ideas=3)
        assert len(router.calls) == 1

        # Profile-biased request → bypasses cache, hits the LLM again.
        biased = await generator.generate(
            call_id, n_ideas=3, org_profile_tenant_id=tenant_id
        )
        assert biased.from_cache is False
        assert len(router.calls) == 2
        # The biased request's prompt carried the org profile.
        biased_payload = json.loads(router.calls[1].messages[0].content)
        assert "organization" in biased_payload
        assert biased_payload["organization"]["sectors"] == ["aerospace"]
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_eligibility_checker_eligible_verdict(
    live_db_pool: asyncpg.Pool,
) -> None:
    """SME in Turkey, TRL in band, no consortium required → ELIGIBLE."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(
        live_db_pool, tenant_id, entity_type="sme", country="tr", trl_current=5
    )
    call_id = await _seed_call(
        live_db_pool,
        geo_scope=["tr"],
        eligibility_tags=["sme"],
        trl_min=3,
        trl_max=7,
        partner_consortium_required=False,
    )
    try:
        checker = EligibilityChecker(pool=live_db_pool, tenant_id=tenant_id)
        report = await checker.check(org_tenant_id=tenant_id, call_id=call_id)
        assert report.verdict == "ELIGIBLE"
        assert report.blockers == []
        assert report.confidence == 1.0
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_eligibility_checker_geo_mismatch_not_eligible(
    live_db_pool: asyncpg.Pool,
) -> None:
    """Turkish org vs a US-only call → a geo blocker → NOT_ELIGIBLE."""

    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(live_db_pool, tenant_id, country="tr")
    call_id = await _seed_call(live_db_pool, geo_scope=["us"])
    try:
        checker = EligibilityChecker(pool=live_db_pool, tenant_id=tenant_id)
        report = await checker.check(org_tenant_id=tenant_id, call_id=call_id)
        assert report.verdict == "NOT_ELIGIBLE"
        assert any("geographic" in b.lower() for b in report.blockers)
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_eligibility_checker_missing_call_raises(
    live_db_pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    try:
        checker = EligibilityChecker(pool=live_db_pool, tenant_id=tenant_id)
        with pytest.raises(ValueError, match="not found"):
            await checker.check(org_tenant_id=tenant_id, call_id=uuid4())
    finally:
        await _cleanup(live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,))


# ════════════════════════════════════════════════════════════════════════
# Endpoint-level integration
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_and_get_idea_endpoint(live_db_pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    router = _MockRouter()
    app = _build_app(pool=live_db_pool, user_id=user_id, router=router)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/ideas",
                json={
                    "title": "Edge-AI maintenance for SMEs",
                    "abstract": "A" * 120,
                    "sectors": ["software"],
                    "keywords": ["ai"],
                    "trl_estimate": 5,
                },
            )
            assert create_resp.status_code == 201
            idea = create_resp.json()
            assert idea["title"] == "Edge-AI maintenance for SMEs"

            get_resp = await client.get(f"/api/v1/ideas/{idea['id']}")
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == idea["id"]

            list_resp = await client.get("/api/v1/ideas")
            assert list_resp.status_code == 200
            assert list_resp.json()["total"] >= 1

        # Embedding was written, not left null.
        async with live_db_pool.acquire() as conn:
            has_embedding = await conn.fetchval(
                "select embedding is not null from project_ideas where id = $1",
                UUID(idea["id"]),
            )
        assert has_embedding is True
    finally:
        await _cleanup(live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,))


@pytest.mark.asyncio
async def test_match_idea_endpoint_then_read_cache(
    live_db_pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(live_db_pool, tenant_id)
    call_id = await _seed_call(live_db_pool)
    idea_id = await _seed_idea(live_db_pool, tenant_id, user_id)
    router = _MockRouter()
    app = _build_app(pool=live_db_pool, user_id=user_id, router=router)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            match_resp = await client.post(f"/api/v1/ideas/{idea_id}/match")
            assert match_resp.status_code == 200
            match_body = match_resp.json()
            assert match_body["idea_id"] == str(idea_id)
            assert len(match_body["matches"]) >= 1
            # Joined call summary fields are present for FE rendering.
            assert match_body["matches"][0]["call_title"]

            cached_resp = await client.get(f"/api/v1/ideas/{idea_id}/matches")
            assert cached_resp.status_code == 200
            assert len(cached_resp.json()["matches"]) == len(match_body["matches"])
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_organization_profile_upsert_roundtrip(
    live_db_pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    router = _MockRouter()
    app = _build_app(pool=live_db_pool, user_id=user_id, router=router)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # No profile yet → 404.
            assert (await client.get("/api/v1/organizations/profile")).status_code == 404

            put_resp = await client.put(
                "/api/v1/organizations/profile",
                json={
                    "entity_type": "sme",
                    "country": "TR",
                    "trl_current": 4,
                    "trl_target": 7,
                    "sectors": ["software", "ai"],
                },
            )
            assert put_resp.status_code == 200
            body = put_resp.json()
            assert body["entity_type"] == "sme"
            # Country is lower-cased server-side for consistent geo matching.
            assert body["country"] == "tr"

            get_resp = await client.get("/api/v1/organizations/profile")
            assert get_resp.status_code == 200
            assert get_resp.json()["trl_target"] == 7
    finally:
        await _cleanup(live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,))


@pytest.mark.asyncio
async def test_generate_ideas_endpoint(live_db_pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    call_id = await _seed_call(live_db_pool)
    router = _MockRouter()
    app = _build_app(pool=live_db_pool, user_id=user_id, router=router)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/calls/{call_id}/generate-ideas",
                json={"n_ideas": 3, "use_org_profile": False},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["call_id"] == str(call_id)
            assert len(body["ideas"]) == 3
            assert body["from_cache"] is False
            assert body["ideas"][0]["alignment_score"] == 0.8
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )


@pytest.mark.asyncio
async def test_call_eligibility_endpoint(live_db_pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _seed_tenant_user(live_db_pool)
    await _seed_org_profile(
        live_db_pool, tenant_id, entity_type="sme", country="tr", trl_current=5
    )
    call_id = await _seed_call(
        live_db_pool, geo_scope=["tr"], eligibility_tags=["sme"], trl_min=3, trl_max=7
    )
    router = _MockRouter()
    app = _build_app(pool=live_db_pool, user_id=user_id, router=router)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/calls/{call_id}/eligibility")
            assert resp.status_code == 200
            body = resp.json()
            assert body["verdict"] == "ELIGIBLE"
            assert len(body["checks"]) == 6  # open/deadline/geo/entity/trl/consortium
            assert all(c["message_tr"] and c["message_en"] for c in body["checks"])
    finally:
        await _cleanup(
            live_db_pool, tenant_ids=(tenant_id,), user_ids=(user_id,), call_ids=(call_id,)
        )
