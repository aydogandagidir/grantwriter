"""Tests for the project-idea endpoints.

Two layers:
  - Pydantic model validation (no DB) — request-body constraints on
    IdeaCreate; runs everywhere.
  - Route-registration smoke — the four idea routes are wired into the
    app and reachable (auth-gated, so we assert on the 401/403 rather
    than a 404).

Full CRUD + matcher integration (live DB + DeterministicEmbedder + mock
LLM router) lands in tests/integration/test_idea_matching_flow.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from src.api.routes.ideas import IdeaCreate
from src.main import create_app

# ── IdeaCreate validation ────────────────────────────────────────────────


def test_idea_create_minimal_valid() -> None:
    idea = IdeaCreate(
        title="Quantum-safe key exchange for IoT",
        abstract=(
            "A lightweight post-quantum key exchange protocol designed for "
            "constrained IoT devices, targeting sub-100ms handshakes."
        ),
    )
    assert idea.source == "user_input"
    assert idea.sectors == []
    assert idea.keywords == []
    assert idea.seed_call_id is None


def test_idea_create_rejects_short_title() -> None:
    with pytest.raises(ValidationError):
        IdeaCreate(title="ab", abstract="x" * 50)


def test_idea_create_rejects_short_abstract() -> None:
    # Abstract must be at least 20 chars — a one-liner isn't enough
    # signal for the embedder.
    with pytest.raises(ValidationError):
        IdeaCreate(title="A reasonable title", abstract="too short")


def test_idea_create_rejects_trl_out_of_range() -> None:
    with pytest.raises(ValidationError):
        IdeaCreate(title="A title here", abstract="x" * 50, trl_estimate=0)
    with pytest.raises(ValidationError):
        IdeaCreate(title="A title here", abstract="x" * 50, trl_estimate=10)


def test_idea_create_rejects_negative_budget() -> None:
    with pytest.raises(ValidationError):
        IdeaCreate(
            title="A title here",
            abstract="x" * 50,
            budget_estimate_eur_min=-1,
        )


def test_idea_create_rejects_extra_fields() -> None:
    """``extra=forbid`` catches typos like ``titel`` before they hit the DB."""

    with pytest.raises(ValidationError):
        IdeaCreate(
            title="A title here",
            abstract="x" * 50,
            titel="oops",  # type: ignore[call-arg]
        )


def test_idea_create_accepts_full_payload() -> None:
    idea = IdeaCreate(
        title="Industrial AI for predictive maintenance",
        abstract="x" * 200,
        technology_angle="Edge-deployed transformer models",
        target_market="Mid-size manufacturers in the EU",
        trl_estimate=5,
        budget_estimate_eur_min=500_000,
        budget_estimate_eur_max=2_000_000,
        team_size_estimate=6,
        sectors=["C29", "J62"],
        keywords=["ai", "predictive maintenance", "edge"],
    )
    assert idea.trl_estimate == 5
    assert idea.sectors == ["C29", "J62"]


# ── Route registration smoke ─────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def unauthed_client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_idea_routes_are_registered(unauthed_client: AsyncClient) -> None:
    """The four idea routes are wired into the app. Without an auth
    header they 401/403 — what matters is they don't 404 (which would
    mean the router never got included)."""

    paths = [
        ("GET", "/api/v1/ideas"),
        ("POST", "/api/v1/ideas"),
        ("GET", "/api/v1/ideas/11111111-1111-1111-1111-111111111111"),
        ("POST", "/api/v1/ideas/11111111-1111-1111-1111-111111111111/match"),
        ("GET", "/api/v1/ideas/11111111-1111-1111-1111-111111111111/matches"),
    ]
    for method, path in paths:
        response = await unauthed_client.request(method, path)
        assert response.status_code in (401, 403), (
            f"{method} {path} returned {response.status_code} — "
            "expected auth rejection, not 404 (router not registered?)"
        )


@pytest.mark.asyncio
async def test_idea_create_validation_runs_before_auth_is_irrelevant(
    unauthed_client: AsyncClient,
) -> None:
    """A POST with no auth is rejected at the auth layer (401/403),
    confirming the route exists and is auth-gated."""

    response = await unauthed_client.post(
        "/api/v1/ideas", json={"title": "x", "abstract": "y"}
    )
    assert response.status_code in (401, 403)
