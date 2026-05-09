"""Endpoint tests for GET /api/v1/proposals/{id}/distinctiveness.

Mocks the JWT dep, the asyncpg dep, and the DistinctivenessScorer so the
test does not need a real signing secret, a database, or OpenAI.
The integration test in tests/compliance/test_distinctiveness_integration.py
exercises the same code path against real Postgres+pgvector.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.api.routes import proposals as proposals_module
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    ProposalNotFoundError,
    ProposalNotReadyError,
    SimilarProject,
)
from src.core.auth import get_current_user_id
from src.core.db import get_db


@pytest.fixture
def overridden_app(app: FastAPI) -> FastAPI:
    """Override JWT + DB deps so endpoint tests don't need a real auth or pool."""

    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
    yield app
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_db, None)


def _patch_scorer(monkeypatch: pytest.MonkeyPatch, behavior: AsyncMock) -> None:
    """Replace ``DistinctivenessScorer`` inside the route module with a stub.

    The stub's ``score`` method delegates to the supplied ``behavior`` async
    callable, so each test can dictate what the scorer returns or raises.
    """

    class StubScorer:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def score(self, *_args: Any, **_kwargs: Any) -> Any:
            return await behavior()

    monkeypatch.setattr(proposals_module, "DistinctivenessScorer", StubScorer)


async def test_returns_200_with_score(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    score = DistinctivenessScore(
        score=0.72,
        level="distinctive",
        message="Your proposal is sufficiently distinctive (closest match: 0.72).",
        similar_projects=[
            SimilarProject(
                cordis_id="c1",
                acronym="ACR",
                title="T",
                similarity=0.72,
                cordis_url="https://cordis.europa.eu/project/id/c1",
            )
        ],
    )

    async def behavior() -> DistinctivenessScore:
        return score

    _patch_scorer(monkeypatch, behavior)

    proposal_id = uuid.uuid4()
    response = await client.get(f"/api/v1/proposals/{proposal_id}/distinctiveness")

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == pytest.approx(0.72)
    assert body["level"] == "distinctive"
    assert body["similar_projects"][0]["acronym"] == "ACR"
    assert body["similar_projects"][0]["cordis_url"] == "https://cordis.europa.eu/project/id/c1"


async def test_returns_404_when_proposal_not_found(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def behavior() -> DistinctivenessScore:
        raise ProposalNotFoundError("proposal abc not found")

    _patch_scorer(monkeypatch, behavior)

    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/distinctiveness")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_returns_422_when_proposal_not_ready(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def behavior() -> DistinctivenessScore:
        raise ProposalNotReadyError("proposal has no draft.excellence_md to score")

    _patch_scorer(monkeypatch, behavior)

    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/distinctiveness")

    assert response.status_code == 422
    assert "excellence_md" in response.json()["detail"]


async def test_requires_auth(client: AsyncClient) -> None:
    """No bearer token + no JWT secret configured → 401 or 503 from auth layer."""

    response = await client.get(f"/api/v1/proposals/{uuid.uuid4()}/distinctiveness")

    # 401 when no Authorization header (HTTPBearer auto_error).
    # 503 if reached the SettingsDep check with supabase_jwt_secret unset.
    assert response.status_code in (401, 403, 503)


async def test_get_db_raises_503_when_pool_not_initialised() -> None:
    """Direct unit test of ``get_db`` — without a pool on app.state, returns 503.

    Tested at the dep level (rather than via the endpoint) because the
    FastAPI dependency-injection layer wraps generator deps in a way that
    makes their pre-yield exceptions awkward to assert on through the HTTP
    surface. The contract we care about is that *the dep itself* signals
    503 — that's what every endpoint using it inherits.
    """
    from unittest.mock import MagicMock

    from fastapi import HTTPException
    from src.core.db import get_db

    fake_request = MagicMock()
    fake_request.app.state = MagicMock(spec=[])  # no db_pool attribute

    with pytest.raises(HTTPException) as exc_info:
        async for _ in get_db(fake_request):
            pass

    assert exc_info.value.status_code == 503
    assert "database" in exc_info.value.detail.lower()
