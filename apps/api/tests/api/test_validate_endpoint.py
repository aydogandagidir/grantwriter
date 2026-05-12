"""Endpoint tests for POST /validate and GET /ai-disclosure.

The compliance reviewer is mocked at the module-level ``ComplianceReviewer``
attribute so we don't have to wire a real LLMRouter — the tests focus on
the route layer (load + dispatch + persist + serialize).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.agents.base import AgentOutput
from src.api.routes import proposals as proposals_module
from src.core.auth import get_current_user_id
from src.core.db import get_db
from src.core.llm_dep import get_llm_router


def _proposal_row(*, draft: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an asyncpg-style proposal row for the validate endpoint."""

    return {
        "tenant_id": uuid4(),
        "programme_id": "horizon_eu_ria",
        "language": "en",
        "brief": "{}",
        "draft": json.dumps(draft or {"excellence_md": "## 1.1\nfoo"}),
    }


def _patch_reviewer(
    monkeypatch: pytest.MonkeyPatch, *, output: dict[str, Any]
) -> None:
    """Replace the ComplianceReviewer class in the route module with a stub."""

    class StubReviewer:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def run(self, _input: Any) -> AgentOutput:
            return AgentOutput(
                agent_id="compliance_reviewer",
                status="completed",
                output=output,
            )

    monkeypatch.setattr(proposals_module, "ComplianceReviewer", StubReviewer)


def _patch_hunter(
    monkeypatch: pytest.MonkeyPatch, *, output: dict[str, Any] | None
) -> None:
    """Replace HallucinationHunter in the route module with a stub.

    ``output=None`` → the agent reports ``status="completed"`` but no
    body, so validate_proposal treats it as "hunt couldn't determine"
    and the response's ``hallucination_hunter`` field falls back to None.
    """

    class StubHunter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def run(self, _input: Any) -> AgentOutput:
            return AgentOutput(
                agent_id="hallucination_hunter",
                status="completed" if output is not None else "skipped",
                output=output or {},
            )

    monkeypatch.setattr(proposals_module, "HallucinationHunter", StubHunter)
    # The route also instantiates a CitationVerifier; stub it so we don't
    # need a real httpx client in the test.

    class StubVerifier:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(proposals_module, "CitationVerifier", StubVerifier)


def _ok_hunt(recommendation: str = "ok") -> dict[str, Any]:
    """Build a HuntReport-shaped dict with the given recommendation."""

    return {
        "total_citations": 4,
        "verified": 4 if recommendation == "ok" else 2,
        "partial_match": 0,
        "fabricated": 0 if recommendation == "ok" else 1,
        "not_found": 0 if recommendation == "ok" else 1,
        "errors": 0,
        "verification_rate": 1.0 if recommendation == "ok" else 0.5,
        "flagged_citations": []
        if recommendation == "ok"
        else [
            {
                "raw_text": "[Smith 2099] fake citation",
                "section": "excellence",
                "status": "fabricated",
                "source": "crossref",
                "match_score": None,
                "warning": "DOI did not resolve",
            }
        ],
        "recommendation": recommendation,
        "claim_check_pass_rate": 0.85 if recommendation == "ok" else 0.4,
    }


@pytest.fixture
def overridden_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    """Override JWT + DB + LLM router deps for endpoint isolation."""

    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()
    # Default fetchrow: a real proposal. Specific tests override.
    fake_conn.fetchrow = AsyncMock(return_value=_proposal_row())
    fake_conn.execute = AsyncMock(return_value="UPDATE 1")

    fake_router = AsyncMock()

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    async def _router_override() -> Any:
        return fake_router

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_llm_router] = _router_override
    # Stash conn for tests that want to inspect calls.
    app.state.test_conn = fake_conn  # type: ignore[attr-defined]
    yield app
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_llm_router, None)


# ── POST /validate ─────────────────────────────────────────────────────


async def test_validate_returns_validation_report_when_clean(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compliance passes + hunt OK → ValidationReport with passed=True,
    no hunt blocker issue, hunt report attached for FE rendering."""

    _patch_reviewer(
        monkeypatch,
        output={
            "passed": True,
            "issues": [],
            "ai_disclosure_text": "Use of AI tools...",
            "compliance_score": 1.0,
        },
    )
    _patch_hunter(monkeypatch, output=_ok_hunt(recommendation="ok"))

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/validate")

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["passed"] is True
    assert body["compliance"]["issues"] == []
    assert body["compliance"]["ai_disclosure_text"] == "Use of AI tools..."
    assert body["compliance"]["compliance_score"] == 1.0
    assert body["hallucination_hunter"]["recommendation"] == "ok"
    assert body["hallucination_hunter"]["claim_check_pass_rate"] == 0.85


async def test_validate_blocks_export_when_hunt_recommends_block(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compliance passes but hunt finds a fabricated citation → response
    flips compliance.passed to False AND appends a blocker issue with
    code='hunt_blocked' so the FE export button disables.

    This is the S3.D13.T1 export-gate that the agent was always meant to
    drive; pre-completion the re-validate endpoint ignored the hunt
    result entirely."""

    _patch_reviewer(
        monkeypatch,
        output={
            "passed": True,
            "issues": [],
            "ai_disclosure_text": "Use of AI tools...",
            "compliance_score": 1.0,
        },
    )
    _patch_hunter(monkeypatch, output=_ok_hunt(recommendation="block_export"))

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/validate")

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["passed"] is False, (
        "hunt block_export MUST flip compliance.passed to False"
    )
    issue_codes = [iss["code"] for iss in body["compliance"]["issues"]]
    assert "hunt_blocked" in issue_codes
    # Severity is blocker so any UI summing blockers sees this one.
    hunt_blocker = next(
        iss for iss in body["compliance"]["issues"] if iss["code"] == "hunt_blocked"
    )
    assert hunt_blocker["severity"] == "blocker"
    assert "fabricated" in hunt_blocker["message_en"].lower() or "unverified" in hunt_blocker["message_en"].lower()
    # Hunt detail is still present so the FE can render the flagged list.
    assert body["hallucination_hunter"]["recommendation"] == "block_export"
    assert body["hallucination_hunter"]["fabricated"] == 1


async def test_validate_persists_compliance_report(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint must update the proposal row with the new report.

    The persisted compliance_report carries both the compliance fields
    AND the nested hallucination_hunter dump, matching the saga's
    persistence shape so existing FE consumers don't have to special-case
    a re-validate vs saga-generated payload."""

    _patch_reviewer(
        monkeypatch,
        output={
            "passed": False,
            "issues": [
                {
                    "severity": "blocker",
                    "section": "excellence",
                    "code": "missing_subsection",
                    "message_tr": "1.2 eksik",
                    "message_en": "1.2 missing",
                }
            ],
            "ai_disclosure_text": "txt",
            "compliance_score": 0.9,
        },
    )
    _patch_hunter(monkeypatch, output=_ok_hunt(recommendation="ok"))

    await client.post(f"/api/v1/proposals/{uuid.uuid4()}/validate")

    fake_conn = overridden_app.state.test_conn
    # First await: fetchrow (load proposal). Second: execute (persist).
    assert fake_conn.execute.await_count >= 1
    update_call = fake_conn.execute.await_args_list[-1]
    sql = update_call.args[0]
    assert "update proposals" in sql.lower()
    # Args: compliance_report json, ai_disclosure_text, proposal_id
    persisted_json = update_call.args[1]
    assert "missing_subsection" in persisted_json
    assert "hallucination_hunter" in persisted_json
    assert update_call.args[2] == "txt"


async def test_validate_returns_404_when_proposal_missing(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(return_value=None)
    # Stub the hunter so the route doesn't try to spin up httpx before
    # the 404 short-circuit (we never reach the hunt in this case, but
    # the stub keeps the test hermetic).
    _patch_hunter(monkeypatch, output=_ok_hunt(recommendation="ok"))

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/validate")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── GET /ai-disclosure ─────────────────────────────────────────────────


async def test_ai_disclosure_returns_text(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(
        return_value={"ai_disclosure_text": "Use of AI tools ..."}
    )
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/ai-disclosure")
    assert response.status_code == 200
    assert response.json()["text"] == "Use of AI tools ..."


async def test_ai_disclosure_returns_404_when_proposal_missing(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(return_value=None)
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/ai-disclosure")
    assert response.status_code == 404


async def test_ai_disclosure_returns_404_when_text_empty(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Proposal exists but disclosure hasn't been generated yet."""

    overridden_app.state.test_conn.fetchrow = AsyncMock(
        return_value={"ai_disclosure_text": None}
    )
    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/ai-disclosure")
    assert response.status_code == 404
    assert "not yet" in response.json()["detail"].lower()
