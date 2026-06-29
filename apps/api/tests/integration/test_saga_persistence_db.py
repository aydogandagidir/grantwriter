"""High-fidelity saga persistence integration test (real Postgres).

The existing saga tests (tests/orchestrator/test_draft_generator.py,
tests/integration/test_he_ria_full_flow.py) drive ``DraftGenerator.run()``
against an ``AsyncMock`` connection — they can assert that ``conn.execute``
was *called*, but never that a row actually landed. The saga's
provenance recording, auto-snapshot, and the ``_persist_outputs`` UPDATE
are all best-effort (try/except-swallowed), so a weak mock-only test
would stay green even if provenance silently wrote zero rows.

This suite closes that gap: it runs the full saga with stub agents (no
LLM, no Anthropic spend) against a REAL Postgres via the ``live_db_pool``
fixture, then queries the DB directly to prove:

- ``proposals`` was updated (draft / status / ai_disclosure_text /
  distinctiveness_score / compliance_report).
- ``proposal_provenance`` rows were upserted, one per writer sentence,
  with correct source / agent / model / token metadata + real content.
- the auto-snapshot ``proposal_versions`` row exists.
- a rerun is idempotent for provenance (upsert keeps rows in sync) while
  cutting a fresh snapshot version.
- the blocking-hunter path persists with ``draft_complete_with_issues``.

Skips when ``TEST_DATABASE_URL`` is unset (CI provisions ``bluedev_test``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.orchestrator.draft_generator import DraftGenerator
from src.orchestrator.provenance_recorder import split_into_sentences
from src.orchestrator.sse_publisher import SSEPublisher

# ── Deterministic writer markdown ──────────────────────────────────────
#
# Kept simple + multi-sentence so ``split_into_sentences`` yields a known,
# recomputable count. We compute the expected provenance-row count from
# these at assert time rather than hardcoding a brittle integer.

EXCELLENCE_MD = (
    "## Excellence\n\n"
    "The objective is to demonstrate a novel method. "
    "Methodology follows established best practice."
)
IMPACT_MD = (
    "## Impact\n\n"
    "Expected impact is a thirty percent productivity gain. "
    "Dissemination targets two peer-reviewed venues."
)
IMPLEMENTATION_MD = (
    "## Implementation\n\n"
    "Work package one runs the pilot. "
    "Work package two evaluates the results."
)

MODEL = "anthropic/claude-opus-4-8"
DISCLOSURE_TEXT = "AI tools were used to draft sections of this proposal."
DISTINCTIVENESS_SCORE = 0.8765


# ── Stub agent ─────────────────────────────────────────────────────────


class StubAgent(BaseAgent):
    """Minimal BaseAgent that returns a pre-canned AgentOutput."""

    agent_id = "stub"
    name = "Stub"
    description = "test stub"
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 1

    def __init__(self, agent_id: str, output: AgentOutput) -> None:
        self.agent_id = agent_id
        self._output = output

    async def run(self, input: AgentInput) -> AgentOutput:
        return self._output

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        yield "ok"


def _writer(agent_id: str, content_key: str, markdown: str) -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        status="completed",
        output={content_key: markdown},
        metadata={"model": MODEL},
        tokens_used={"input": 600, "output": 600},
    )


def _ok(agent_id: str, output: dict[str, Any]) -> AgentOutput:
    return AgentOutput(agent_id=agent_id, status="completed", output=output)


def _build_agents(*, recommendation: str = "proceed") -> dict[str, BaseAgent]:
    """All seven agents as deterministic stubs.

    ``recommendation`` flips the hallucination hunter between the happy
    path (``proceed`` → ``draft_complete``) and the blocking path
    (``block_export`` → ``draft_complete_with_issues``).
    """

    return {
        "call_analyst": StubAgent(
            "call_analyst", _ok("call_analyst", {"key_terms_to_use": []})
        ),
        "excellence_writer": StubAgent(
            "excellence_writer",
            _writer("excellence_writer", "excellence_md", EXCELLENCE_MD),
        ),
        "impact_writer": StubAgent(
            "impact_writer", _writer("impact_writer", "impact_md", IMPACT_MD)
        ),
        "implementation_writer": StubAgent(
            "implementation_writer",
            _writer("implementation_writer", "implementation_md", IMPLEMENTATION_MD),
        ),
        "compliance_reviewer": StubAgent(
            "compliance_reviewer",
            _ok(
                "compliance_reviewer",
                {
                    "passed": True,
                    "issues": [],
                    "ai_disclosure_text": DISCLOSURE_TEXT,
                    "compliance_score": 1.0,
                },
            ),
        ),
        "distinctiveness_scorer": StubAgent(
            "distinctiveness_scorer",
            _ok(
                "distinctiveness_scorer",
                {
                    "score": DISTINCTIVENESS_SCORE,
                    "level": "distinctive",
                    "message": "ok",
                },
            ),
        ),
        "hallucination_hunter": StubAgent(
            "hallucination_hunter",
            _ok("hallucination_hunter", {"recommendation": recommendation}),
        ),
    }


def _expected_provenance_count() -> int:
    return (
        len(split_into_sentences(EXCELLENCE_MD))
        + len(split_into_sentences(IMPACT_MD))
        + len(split_into_sentences(IMPLEMENTATION_MD))
    )


# ── DB fixtures ────────────────────────────────────────────────────────


async def _seed_owned_proposal(
    conn: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed tenant + owner (auth + public.users) + proposal.

    Returns (tenant_id, user_id, proposal_id). A real owner is seeded so
    the completion-email lookup + the auto-snapshot audit write both
    exercise their populated paths.
    """

    suffix = uuid.uuid4()
    tenant_id = await conn.fetchval(
        "insert into tenants(name, slug) values ($1, $2) returning id",
        f"saga-it-{suffix}",
        f"saga-it-{suffix}",
    )
    user_id = uuid.uuid4()
    await conn.execute(
        "insert into auth.users(id, email) values ($1, $2)",
        user_id,
        f"owner-{suffix}@example.com",
    )
    await conn.execute(
        "insert into public.users(id, tenant_id, role) values ($1, $2, 'owner')",
        user_id,
        tenant_id,
    )
    proposal_id = await conn.fetchval(
        """
        insert into proposals(
          tenant_id, created_by, programme_id, title, language, status
        )
        values ($1, $2, 'horizon_eu_ria', 'Saga IT proposal', 'en', 'draft')
        returning id
        """,
        tenant_id,
        user_id,
    )
    return tenant_id, user_id, proposal_id


async def _cleanup(
    conn: asyncpg.Connection, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    # Tear down deepest-first to respect FKs that don't cascade:
    #  - audit_log.tenant_id is NOT cascade-deleted (legal retention).
    #  - proposal_versions.created_by references public.users, so the
    #    version rows must go before the owner row.
    # Deleting proposals explicitly cascades to proposal_versions +
    # proposal_provenance (both ON DELETE CASCADE on proposal_id).
    await conn.execute("delete from audit_log where tenant_id = $1", tenant_id)
    await conn.execute("delete from proposals where tenant_id = $1", tenant_id)
    await conn.execute("delete from tenants where id = $1", tenant_id)
    await conn.execute("delete from public.users where id = $1", user_id)
    await conn.execute("delete from auth.users where id = $1", user_id)


@pytest.fixture(autouse=True)
def _stub_completion_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the saga hermetic — never attempt a real Resend send.

    The saga calls ``send_draft_complete_email`` on the success path; we
    stub it to a no-op that reports ``status='sent'`` so the (best-effort)
    email branch runs without any network I/O or audit row.
    """

    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def _fake_send(**_kwargs: Any) -> Any:
        return SimpleNamespace(status="sent", template_name="draft_complete")

    monkeypatch.setattr(
        "src.orchestrator.draft_generator.send_draft_complete_email",
        AsyncMock(side_effect=_fake_send),
    )


# ── Tests ──────────────────────────────────────────────────────────────


async def test_full_saga_persists_draft_provenance_and_snapshot(
    live_db_pool: asyncpg.Pool,
) -> None:
    async with live_db_pool.acquire() as conn:
        tenant_id, user_id, proposal_id = await _seed_owned_proposal(conn)
        try:
            publisher = SSEPublisher(proposal_id)
            saga = DraftGenerator(
                proposal_id=proposal_id,
                agents=_build_agents(recommendation="proceed"),
                publisher=publisher,
                conn=conn,
            )
            result = await saga.run()

            # 1. Returned status.
            assert result.status == "draft_complete"

            # 2. proposals.status actually persisted (not just execute called).
            db_status = await conn.fetchval(
                "select status from proposals where id = $1", proposal_id
            )
            assert db_status == "draft_complete"

            # 3. proposals.draft holds the three writer sections.
            draft_raw = await conn.fetchval(
                "select draft from proposals where id = $1", proposal_id
            )
            draft = json.loads(draft_raw) if isinstance(draft_raw, str) else draft_raw
            assert draft["excellence_md"] == EXCELLENCE_MD
            assert draft["impact_md"] == IMPACT_MD
            assert draft["implementation_md"] == IMPLEMENTATION_MD

            # 4. ai_disclosure_text persisted from the compliance stub.
            assert (
                await conn.fetchval(
                    "select ai_disclosure_text from proposals where id = $1",
                    proposal_id,
                )
                == DISCLOSURE_TEXT
            )

            # 5. distinctiveness_score persisted (numeric → Decimal).
            score = await conn.fetchval(
                "select distinctiveness_score from proposals where id = $1",
                proposal_id,
            )
            assert float(score) == pytest.approx(DISTINCTIVENESS_SCORE)

            # 6. compliance_report carries the nested hunter dump.
            cr_raw = await conn.fetchval(
                "select compliance_report from proposals where id = $1", proposal_id
            )
            compliance_report = json.loads(cr_raw) if isinstance(cr_raw, str) else cr_raw
            assert "hallucination_hunter" in compliance_report

            # 7. Provenance row count == expected sentence total (THE GAP).
            prov_count = await conn.fetchval(
                "select count(*) from proposal_provenance where proposal_id = $1",
                proposal_id,
            )
            assert prov_count == _expected_provenance_count()

            # 8. All three sections covered.
            sections = {
                row["section"]
                for row in await conn.fetch(
                    "select distinct section from proposal_provenance "
                    "where proposal_id = $1",
                    proposal_id,
                )
            }
            assert sections == {"excellence", "impact", "implementation"}

            # 9. Metadata round-trips on every row.
            rows = await conn.fetch(
                "select source, agent_id, llm_model, llm_tokens "
                "from proposal_provenance where proposal_id = $1",
                proposal_id,
            )
            assert all(r["source"] == "ai-generated" for r in rows)
            assert all(r["llm_model"] == MODEL for r in rows)
            assert all(r["llm_tokens"] == 1200 for r in rows)
            assert {r["agent_id"] for r in rows} == {
                "excellence_writer",
                "impact_writer",
                "implementation_writer",
            }

            # 10. Real sentence text landed (not placeholders).
            contents = [
                r["content"]
                for r in await conn.fetch(
                    "select content from proposal_provenance where proposal_id = $1",
                    proposal_id,
                )
            ]
            assert any("Methodology follows established best practice." in c for c in contents)

            # 11. Auto-snapshot version row exists.
            snap = await conn.fetchrow(
                "select version_number, comment, draft_snapshot "
                "from proposal_versions where proposal_id = $1",
                proposal_id,
            )
            assert snap is not None
            assert snap["version_number"] == 1
            assert snap["comment"] == "auto-snapshot after generation"

            # 12. Snapshot captured the persisted draft.
            snap_draft = (
                json.loads(snap["draft_snapshot"])
                if isinstance(snap["draft_snapshot"], str)
                else snap["draft_snapshot"]
            )
            assert snap_draft == draft

            # 13. SSE completion event published.
            assert any(
                e["event"] == "completed" for e in publisher.events
            ), [e["event"] for e in publisher.events]
            assert result.sse_events_published == len(publisher.events)
        finally:
            await _cleanup(conn, tenant_id=tenant_id, user_id=user_id)


async def test_blocking_hunter_persists_with_issues_status(
    live_db_pool: asyncpg.Pool,
) -> None:
    async with live_db_pool.acquire() as conn:
        tenant_id, user_id, proposal_id = await _seed_owned_proposal(conn)
        try:
            saga = DraftGenerator(
                proposal_id=proposal_id,
                agents=_build_agents(recommendation="block_export"),
                publisher=SSEPublisher(proposal_id),
                conn=conn,
            )
            result = await saga.run()

            assert result.status == "draft_complete_with_issues"
            assert (
                await conn.fetchval(
                    "select status from proposals where id = $1", proposal_id
                )
                == "draft_complete_with_issues"
            )
            # The blocking path still persists provenance + snapshot.
            assert (
                await conn.fetchval(
                    "select count(*) from proposal_provenance where proposal_id = $1",
                    proposal_id,
                )
                == _expected_provenance_count()
            )
            assert (
                await conn.fetchval(
                    "select count(*) from proposal_versions where proposal_id = $1",
                    proposal_id,
                )
                == 1
            )
        finally:
            await _cleanup(conn, tenant_id=tenant_id, user_id=user_id)


async def test_saga_rerun_is_idempotent_for_provenance(
    live_db_pool: asyncpg.Pool,
) -> None:
    async with live_db_pool.acquire() as conn:
        tenant_id, user_id, proposal_id = await _seed_owned_proposal(conn)
        try:
            agents = _build_agents(recommendation="proceed")
            await DraftGenerator(
                proposal_id=proposal_id,
                agents=agents,
                publisher=SSEPublisher(proposal_id),
                conn=conn,
            ).run()
            first_count = await conn.fetchval(
                "select count(*) from proposal_provenance where proposal_id = $1",
                proposal_id,
            )

            # Re-run the same saga — deterministic sentence_id (content hash)
            # means the upsert updates rows in place, not duplicates.
            await DraftGenerator(
                proposal_id=proposal_id,
                agents=agents,
                publisher=SSEPublisher(proposal_id),
                conn=conn,
            ).run()
            second_count = await conn.fetchval(
                "select count(*) from proposal_provenance where proposal_id = $1",
                proposal_id,
            )

            assert first_count == _expected_provenance_count()
            assert second_count == first_count  # upsert kept rows in sync

            # But a rerun cuts a fresh snapshot version (1 → 2).
            versions = await conn.fetchval(
                "select count(*) from proposal_versions where proposal_id = $1",
                proposal_id,
            )
            assert versions == 2
        finally:
            await _cleanup(conn, tenant_id=tenant_id, user_id=user_id)
