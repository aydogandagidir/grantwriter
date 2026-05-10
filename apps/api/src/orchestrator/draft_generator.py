"""Saga orchestrator — runs the 7-agent draft generation flow.

Per docs/06-agent-architecture.md §5. Five sequential / parallel steps:

1. Call Analyst (must succeed — proposal unusable without it)
2. Excellence Writer ‖ Impact Writer (parallel; either failing → recoverable)
3. Implementation Writer (depends on Excellence + Impact)
4. Compliance Reviewer ‖ Distinctiveness Scorer (parallel, non-critical)
5. Hallucination Hunter (final pass — its recommendation flips the
   proposal status to ``draft_complete_with_issues`` when blocking)

Failure model: agents themselves never raise per
:class:`BaseAgent.run` contract — they return ``status="failed"`` with
metadata.error. The orchestrator inspects ``AgentOutput.status`` and
maps to ``draft_complete`` / ``draft_complete_with_issues`` /
``failed_recoverable`` / ``failed`` per docs/06 §5. Unexpected exceptions
from agent code are caught defensively and treated as a failed step.

Test seam: pass a custom ``agents`` dict at construction time. Production
wiring uses :func:`build_default_agents` to instantiate the real seven
with shared LLMRouter, CitationVerifier, and asyncpg.Connection.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.agents import (
    CallAnalyst,
    ComplianceReviewer,
    ExcellenceWriter,
    HallucinationHunter,
    ImpactWriter,
    ImplementationWriter,
)
from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.agents.distinctiveness_scorer import DistinctivenessScorerAgent
from src.core.audit import write_audit_event
from src.core.config import get_settings
from src.notifications import send_draft_complete_email
from src.orchestrator.sse_publisher import SSEPublisher

if TYPE_CHECKING:
    import asyncpg

    from src.citations import CitationVerifier
    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


DraftStatus = Literal[
    "draft_complete",
    "draft_complete_with_issues",
    "failed_recoverable",
    "failed",
]
"""Final saga statuses. Aligns with the ``proposals.status`` CHECK
constraint in migration 005, plus the orchestrator-internal failure
modes."""


_AGENT_ORDER: list[str] = [
    "call_analyst",
    "excellence_writer",
    "impact_writer",
    "implementation_writer",
    "compliance_reviewer",
    "distinctiveness_scorer",
    "hallucination_hunter",
]


class RecoverableError(Exception):
    """Saga failure where re-running the user's draft generation makes sense."""


class UnrecoverableError(Exception):
    """Saga failure where re-running won't help (proposal data missing, etc.)."""


class ProposalNotFoundError(UnrecoverableError):
    """Proposal row doesn't exist for the given id."""


class DraftGeneratorResult(BaseModel):
    """Top-level saga result — the Celery task result + the test assertion seam."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    status: DraftStatus
    agent_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Per-agent ``AgentOutput.model_dump(mode='json')`` keyed by agent_id."""
    sse_events_published: int = 0
    duration_ms: int = 0
    error: str | None = None


class DraftGenerator:
    """Orchestrate one saga run for a single proposal.

    Construct fresh per generation request. Holds no state across runs.
    """

    def __init__(
        self,
        proposal_id: UUID,
        *,
        agents: dict[str, BaseAgent],
        publisher: SSEPublisher,
        conn: asyncpg.Connection,
    ) -> None:
        self.proposal_id = proposal_id
        self._agents = agents
        self._publisher = publisher
        self._conn = conn

        missing = [a for a in _AGENT_ORDER if a not in agents]
        if missing:
            raise ValueError(f"missing agents in registry: {missing}")

    # ── Public entry point ────────────────────────────────────────────

    async def run(self) -> DraftGeneratorResult:  # noqa: PLR0911
        # Guard-clause returns per saga step — flattening this into one
        # exit point would obscure the failure routing.
        started = time.perf_counter()
        agent_outputs: dict[str, AgentOutput] = {}

        try:
            proposal_row = await self._load_proposal()
        except ProposalNotFoundError as exc:
            return self._build_result(
                "failed",
                started,
                agent_outputs,
                error=str(exc),
            )

        try:
            agent_input = self._initial_input(proposal_row)
            await self._update_status("generating")
            await self._publisher.publish(
                "saga_started", {"proposal_id": str(self.proposal_id)}
            )

            # Step 1 — Call Analyst (critical)
            call_output = await self._run_step("call_analyst", agent_input)
            agent_outputs["call_analyst"] = call_output
            if call_output.status != "completed":
                return await self._fail_and_persist(
                    "failed",
                    started,
                    agent_outputs,
                    f"Call Analyst failed: {call_output.metadata.get('error', '')}",
                )
            agent_input = self._with_output(agent_input, "call_analyst", call_output)

            # Step 2 — Excellence ‖ Impact (parallel, both required)
            excellence_output, impact_output = await self._run_pair(
                "excellence_writer", "impact_writer", agent_input
            )
            agent_outputs["excellence_writer"] = excellence_output
            agent_outputs["impact_writer"] = impact_output
            if excellence_output.status != "completed" or impact_output.status != "completed":
                return await self._fail_and_persist(
                    "failed_recoverable",
                    started,
                    agent_outputs,
                    "Excellence or Impact writer failed",
                )
            agent_input = self._with_output(
                agent_input, "excellence_writer", excellence_output
            )
            agent_input = self._with_output(
                agent_input, "impact_writer", impact_output
            )

            # Step 3 — Implementation (sequential)
            impl_output = await self._run_step("implementation_writer", agent_input)
            agent_outputs["implementation_writer"] = impl_output
            if impl_output.status != "completed":
                return await self._fail_and_persist(
                    "failed_recoverable",
                    started,
                    agent_outputs,
                    "Implementation writer failed",
                )
            agent_input = self._with_output(
                agent_input, "implementation_writer", impl_output
            )

            # Step 4 — Compliance ‖ Distinctiveness (parallel, non-critical)
            compliance_output, distinctiveness_output = await self._run_pair(
                "compliance_reviewer", "distinctiveness_scorer", agent_input
            )
            agent_outputs["compliance_reviewer"] = compliance_output
            agent_outputs["distinctiveness_scorer"] = distinctiveness_output
            # Non-critical: skipped/failed both OK to continue. Compliance
            # surfaces issues for the UI; distinctiveness is advisory.
            agent_input = self._with_output(
                agent_input, "compliance_reviewer", compliance_output
            )
            agent_input = self._with_output(
                agent_input, "distinctiveness_scorer", distinctiveness_output
            )

            # Step 5 — Hallucination Hunter (final pass)
            hunt_output = await self._run_step("hallucination_hunter", agent_input)
            agent_outputs["hallucination_hunter"] = hunt_output
            if hunt_output.status != "completed":
                return await self._fail_and_persist(
                    "failed_recoverable",
                    started,
                    agent_outputs,
                    "Hallucination Hunter failed",
                )

            # Persist + final status
            await self._persist_outputs(agent_outputs)
            final_status: DraftStatus = (
                "draft_complete_with_issues"
                if _hunt_is_blocking(hunt_output)
                else "draft_complete"
            )
            await self._update_status(final_status)
            await self._publisher.publish(
                "completed",
                {"proposal_id": str(self.proposal_id), "status": final_status},
            )
            # Best-effort completion email — outside the saga's blocking
            # path so a Resend outage never flips a green run to red.
            await self._send_completion_email(final_status)
            return self._build_result(final_status, started, agent_outputs)

        except UnrecoverableError as exc:
            logger.exception("saga_unrecoverable", extra={"proposal_id": str(self.proposal_id)})
            return await self._fail_and_persist(
                "failed", started, agent_outputs, f"Unrecoverable: {exc}"
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception(
                "saga_unexpected_error", extra={"proposal_id": str(self.proposal_id)}
            )
            return await self._fail_and_persist(
                "failed_recoverable",
                started,
                agent_outputs,
                f"Unexpected error: {exc}",
            )

    # ── Step runners ──────────────────────────────────────────────────

    async def _run_step(self, agent_id: str, agent_input: AgentInput) -> AgentOutput:
        agent = self._agents[agent_id]
        await self._publisher.publish(
            "agent_started",
            {
                "agent": agent_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        try:
            output = await agent.run(agent_input)
        except Exception as exc:
            # Defensive — agents shouldn't raise (BaseAgent.run contract),
            # but if one does we don't want to abort the whole saga before
            # the orchestrator's fail-and-persist path runs.
            logger.exception(
                "agent_raised_unexpectedly",
                extra={"agent": agent_id, "proposal_id": str(self.proposal_id)},
            )
            output = AgentOutput(
                agent_id=agent_id,
                status="failed",
                metadata={"error": f"unhandled exception: {exc}"},
            )

        event_type = "agent_completed" if output.status == "completed" else "agent_failed"
        await self._publisher.publish(
            event_type,
            {
                "agent": agent_id,
                "status": output.status,
                "duration_ms": output.duration_ms,
            },
        )
        return output

    async def _run_pair(
        self,
        agent_a: str,
        agent_b: str,
        agent_input: AgentInput,
    ) -> tuple[AgentOutput, AgentOutput]:
        """Run two agents concurrently; never raises (each call is wrapped)."""

        # gather without return_exceptions because _run_step already wraps
        # raises into failed AgentOutput — both branches always produce
        # a valid AgentOutput object.
        import asyncio as _asyncio

        a, b = await _asyncio.gather(
            self._run_step(agent_a, agent_input),
            self._run_step(agent_b, agent_input),
        )
        return a, b

    # ── DB I/O ────────────────────────────────────────────────────────

    async def _load_proposal(self) -> dict[str, Any]:
        """Read the proposal row + linked call into a dict.

        Selects only the columns the saga needs to assemble the initial
        AgentInput. Raises :class:`ProposalNotFoundError` if the id is
        bogus — caller maps that to ``status="failed"`` so the user gets
        a clear signal rather than a Celery retry storm.
        """

        row = await self._conn.fetchrow(
            """
            select
              p.id,
              p.tenant_id,
              p.programme_id,
              p.language,
              p.brief,
              p.call_id
            from proposals p
            where p.id = $1
            """,
            self.proposal_id,
        )
        if row is None:
            raise ProposalNotFoundError(
                f"proposal {self.proposal_id} not found"
            )

        call: dict[str, Any] = {}
        if row["call_id"] is not None:
            call_row = await self._conn.fetchrow(
                "select call_text, raw_metadata from calls where id = $1",
                row["call_id"],
            )
            if call_row is not None:
                call["call_text"] = call_row["call_text"] or ""
                # raw_metadata is whatever the scraper persisted — pass
                # through; downstream agents pull what they need.
                if call_row["raw_metadata"]:
                    call.update(_loads(call_row["raw_metadata"]))

        return {
            "tenant_id": row["tenant_id"],
            "programme_id": row["programme_id"],
            "language": row["language"],
            "brief": _loads(row["brief"]) if row["brief"] else {},
            "call": call,
        }

    async def _persist_outputs(
        self, agent_outputs: dict[str, AgentOutput]
    ) -> None:
        """Write all agent outputs back to the proposals row.

        The whole update is one statement so it's atomic — partial
        persistence would leave the proposal in a confusing state.
        """

        draft = _build_draft_dict(agent_outputs)
        compliance_dump = _output_dict(agent_outputs.get("compliance_reviewer"))
        distinctiveness_dump = _output_dict(agent_outputs.get("distinctiveness_scorer"))
        hunt_dump = _output_dict(agent_outputs.get("hallucination_hunter"))

        ai_disclosure_text = compliance_dump.get("ai_disclosure_text")
        distinctiveness_score = distinctiveness_dump.get("score")

        compliance_report = {
            **compliance_dump,
            "hallucination_hunter": hunt_dump,
        }

        await self._conn.execute(
            """
            update proposals
            set draft = $1::jsonb,
                compliance_report = $2::jsonb,
                ai_disclosure_text = $3,
                distinctiveness_score = $4,
                updated_at = now()
            where id = $5
            """,
            json.dumps(draft),
            json.dumps(compliance_report),
            ai_disclosure_text,
            distinctiveness_score,
            self.proposal_id,
        )

    async def _update_status(self, status: str) -> None:
        await self._conn.execute(
            "update proposals set status = $1, updated_at = now() where id = $2",
            status,
            self.proposal_id,
        )

    async def _send_completion_email(self, final_status: DraftStatus) -> None:
        """Notify the proposal owner that the saga finished.

        Pulls the title + owner email + tenant language in one
        round-trip, composes the proposal URL from ``settings.app_url``,
        and fans out via :func:`send_draft_complete_email`. **Every**
        failure mode is swallowed — a Resend / DB / mocked-fixture
        hiccup must never flip a green saga to ``failed_recoverable``.
        """

        try:
            await self._send_completion_email_unsafe(final_status)
        except Exception:
            logger.exception(
                "draft_complete_email_unexpected_failure",
                extra={"proposal_id": str(self.proposal_id)},
            )

    async def _send_completion_email_unsafe(
        self, final_status: DraftStatus
    ) -> None:
        """The actual lookup + send. Wrapped by :meth:`_send_completion_email`
        so missing-column / network failures never bubble up.
        """

        row = await self._conn.fetchrow(
            """
            select p.title, p.language, p.tenant_id, p.created_by,
                   au.email as owner_email
              from proposals p
              left join auth.users au on au.id = p.created_by
             where p.id = $1
            """,
            self.proposal_id,
        )
        if row is None:
            return

        owner_email = _row_get(row, "owner_email")
        if not owner_email:
            # Proposal may have been deleted, or created_by was nulled
            # by KVKK/GDPR self-deletion. Either way: nothing to send.
            return

        settings = get_settings()
        proposal_url = (
            f"{settings.app_url.rstrip('/')}/proposals/{self.proposal_id}"
        )
        title = _row_get(row, "title") or "(untitled)"
        language = _row_get(row, "language")

        result = await send_draft_complete_email(
            to=str(owner_email),
            proposal_id=self.proposal_id,
            proposal_title=str(title),
            proposal_url=proposal_url,
            status=final_status,
            has_blockers=final_status == "draft_complete_with_issues",
            lang=str(language) if language else None,
        )

        if result.status == "failed":
            try:
                await write_audit_event(
                    self._conn,
                    tenant_id=_row_get(row, "tenant_id"),
                    user_id=_row_get(row, "created_by"),
                    action="email.send_failed",
                    resource_type="proposal",
                    resource_id=self.proposal_id,
                    diff={"template": result.template_name},
                )
            except Exception:
                logger.exception(
                    "email_send_failed_audit_write_failed",
                    extra={"proposal_id": str(self.proposal_id)},
                )

    # ── Helpers ───────────────────────────────────────────────────────

    def _initial_input(self, proposal_row: dict[str, Any]) -> AgentInput:
        return AgentInput(
            proposal_id=self.proposal_id,
            tenant_id=proposal_row["tenant_id"],
            programme_id=str(proposal_row["programme_id"]),
            language=proposal_row["language"],
            brief=proposal_row["brief"],
            call=proposal_row["call"],
            previous_outputs={},
        )

    @staticmethod
    def _with_output(
        agent_input: AgentInput, agent_id: str, output: AgentOutput
    ) -> AgentInput:
        """Append an agent output to ``previous_outputs`` and return a new input.

        ``AgentInput`` is frozen, so we model_copy with the merged dict.
        """

        new_previous = {**agent_input.previous_outputs, agent_id: output.model_dump(mode="json")}
        return agent_input.model_copy(update={"previous_outputs": new_previous})

    async def _fail_and_persist(
        self,
        status: DraftStatus,
        started: float,
        agent_outputs: dict[str, AgentOutput],
        error: str,
    ) -> DraftGeneratorResult:
        """Common failure path — update DB status + publish error event + return result."""

        await self._update_status(status)
        await self._publisher.publish(
            "error",
            {
                "error": error,
                "recoverable": status == "failed_recoverable",
            },
        )
        return self._build_result(status, started, agent_outputs, error=error)

    def _build_result(
        self,
        status: DraftStatus,
        started: float,
        agent_outputs: dict[str, AgentOutput],
        *,
        error: str | None = None,
    ) -> DraftGeneratorResult:
        return DraftGeneratorResult(
            proposal_id=self.proposal_id,
            status=status,
            agent_outputs={
                k: v.model_dump(mode="json") for k, v in agent_outputs.items()
            },
            sse_events_published=len(self._publisher.events),
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )


# ── Default agent factory ────────────────────────────────────────────


def build_default_agents(
    *,
    router: LLMRouter,
    verifier: CitationVerifier,
    compliance_conn: asyncpg.Connection,
    distinctiveness_conn: asyncpg.Connection,
) -> dict[str, BaseAgent]:
    """Wire up the seven concrete agents with their dependencies.

    Compliance and Distinctiveness run in parallel in step 4 of the
    saga and both touch Postgres — a single asyncpg connection cannot
    serve concurrent queries, so the caller MUST hand in two separate
    connections (acquired from the pool). Tests bypass this factory
    entirely and pass mock agents into :class:`DraftGenerator` directly.
    """

    return {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(router=router, conn=compliance_conn),
        "distinctiveness_scorer": DistinctivenessScorerAgent(conn=distinctiveness_conn),
        "hallucination_hunter": HallucinationHunter(verifier=verifier),
    }


# ── Pure helpers (also test surfaces) ────────────────────────────────


def _hunt_is_blocking(hunt_output: AgentOutput) -> bool:
    """The hunter recommends blocking when fabricated/not_found > 0."""

    if hunt_output.status != "completed":
        return False
    payload = hunt_output.output or {}
    return str(payload.get("recommendation") or "").lower() == "block_export"


def _build_draft_dict(agent_outputs: dict[str, AgentOutput]) -> dict[str, Any]:
    """Pull the three section markdowns out of the writer outputs.

    Empty string when a writer didn't produce content (failed step) so
    the JSON stays well-formed.
    """

    draft: dict[str, Any] = {}
    for agent_id, key in (
        ("excellence_writer", "excellence_md"),
        ("impact_writer", "impact_md"),
        ("implementation_writer", "implementation_md"),
    ):
        output = agent_outputs.get(agent_id)
        draft[key] = ""
        if output is not None and output.status == "completed":
            draft[key] = str(output.output.get(key) or "")
    return draft


def _output_dict(output: AgentOutput | None) -> dict[str, Any]:
    """Safe ``output`` extraction — returns empty dict when agent was skipped/failed."""

    if output is None:
        return {}
    return dict(output.output or {})


def _row_get(row: Any, key: str) -> Any:
    """Tolerant column access for asyncpg.Record + dict + Mock fixtures.

    asyncpg.Record supports ``row[key]`` when the column is present and
    raises ``KeyError`` otherwise. Test mocks usually return plain dicts
    that may omit columns we now look up. Both shapes implement
    ``__contains__``; this helper degrades silently to ``None``.
    """

    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _loads(value: Any) -> dict[str, Any]:
    """Coerce asyncpg's jsonb returns (str | dict | None) into a dict."""

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = [
    "DraftGenerator",
    "DraftGeneratorResult",
    "DraftStatus",
    "ProposalNotFoundError",
    "RecoverableError",
    "UnrecoverableError",
    "build_default_agents",
]
