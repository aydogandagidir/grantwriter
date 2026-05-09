"""Endpoint tests for GET /api/v1/proposals/{id}/distinctiveness.

Uses FastAPI dependency_overrides so we don't need a live DB or a real JWT.
The integration test in tests/compliance/test_distinctiveness_integration.py
exercises the same endpoint against real Postgres.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from src.api.v1 import proposals as proposals_module
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    ProposalNotFoundError,
    ProposalNotReadyError,
    SimilarProject,
)
from src.core.auth import User, get_current_user
from src.core.db import get_db
from src.main import app


@pytest.fixture
def client_with_overrides() -> Any:
    fake_user = User(id=uuid4(), email="test@example.com")
    fake_conn = AsyncMock()

    async def _user_override() -> User:
        return fake_user

    async def _db_override() -> Any:
        yield fake_conn

    app.dependency_overrides[get_current_user] = _user_override
    app.dependency_overrides[get_db] = _db_override
    try:
        # The TestClient drives the lifespan, but our lifespan opens an asyncpg
        # pool we don't want here. Bypass by overriding lifespan to a no-op.
        original_lifespan = app.router.lifespan_context

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def noop_lifespan(_: Any):
            yield

        app.router.lifespan_context = noop_lifespan
        with TestClient(app) as client:
            yield client, fake_conn
        app.router.lifespan_context = original_lifespan
    finally:
        app.dependency_overrides.clear()


def _patch_scorer(monkeypatch: pytest.MonkeyPatch, behavior: AsyncMock) -> None:
    class StubScorer:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def score(self, *_args: Any, **_kwargs: Any) -> Any:
            return await behavior()

    monkeypatch.setattr(proposals_module, "DistinctivenessScorer", StubScorer)


def test_returns_200_with_score(
    client_with_overrides: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_with_overrides
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

    proposal_id = uuid4()
    response = client.get(f"/api/v1/proposals/{proposal_id}/distinctiveness")
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == pytest.approx(0.72)
    assert body["level"] == "distinctive"
    assert body["similar_projects"][0]["acronym"] == "ACR"


def test_returns_404_when_proposal_not_found(
    client_with_overrides: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_with_overrides

    async def behavior() -> DistinctivenessScore:
        raise ProposalNotFoundError("not found")

    _patch_scorer(monkeypatch, behavior)

    response = client.get(f"/api/v1/proposals/{uuid4()}/distinctiveness")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_returns_422_when_proposal_not_ready(
    client_with_overrides: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_with_overrides

    async def behavior() -> DistinctivenessScore:
        raise ProposalNotReadyError("no excellence_md")

    _patch_scorer(monkeypatch, behavior)

    response = client.get(f"/api/v1/proposals/{uuid4()}/distinctiveness")
    assert response.status_code == 422
    assert "excellence_md" in response.json()["detail"]


def test_health_endpoint_exists() -> None:
    """Sanity check that the FastAPI app is wired up correctly."""
    from contextlib import asynccontextmanager

    original = app.router.lifespan_context

    @asynccontextmanager
    async def noop(_: Any):
        yield

    app.router.lifespan_context = noop
    try:
        with TestClient(app) as c:
            r = c.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
    finally:
        app.router.lifespan_context = original
