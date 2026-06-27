"""Tests for the organization-profile endpoints.

Pydantic model validation (no DB) + route-registration smoke. Full
upsert/read roundtrip against a live DB lands in the integration suite.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from src.api.routes.organizations import OrganizationProfileUpsert
from src.main import create_app

# ── OrganizationProfileUpsert validation ─────────────────────────────────


def test_org_profile_empty_body_is_valid() -> None:
    """An empty body is allowed — the form is filled progressively."""

    profile = OrganizationProfileUpsert()
    assert profile.entity_type is None
    assert profile.nace_codes == []
    assert profile.sectors == []
    assert profile.past_projects == []


def test_org_profile_accepts_known_entity_types() -> None:
    for entity_type in (
        "individual",
        "sme",
        "university",
        "large_corp",
        "ngo",
        "research_org",
    ):
        profile = OrganizationProfileUpsert(entity_type=entity_type)  # type: ignore[arg-type]
        assert profile.entity_type == entity_type


def test_org_profile_rejects_unknown_entity_type() -> None:
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(entity_type="startup")  # type: ignore[arg-type]


def test_org_profile_rejects_trl_out_of_range() -> None:
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(trl_current=0)
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(trl_current=10)
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(trl_target=11)


def test_org_profile_rejects_implausible_founded_year() -> None:
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(founded_year=1500)
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(founded_year=2200)


def test_org_profile_rejects_negative_team_size() -> None:
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(team_size=-1)


def test_org_profile_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        OrganizationProfileUpsert(employees=50)  # type: ignore[call-arg]


def test_org_profile_accepts_full_payload() -> None:
    profile = OrganizationProfileUpsert(
        legal_name="Bluedev Yazılım A.Ş.",
        entity_type="sme",
        country="TR",
        nuts_region="TR510",
        nace_codes=["J62"],
        sectors=["software", "ai"],
        team_size=12,
        annual_revenue_eur=1_200_000,
        founded_year=2022,
        technology_areas=["nlp", "rag"],
        trl_current=4,
        trl_target=7,
        expertise_keywords=["llm", "grant writing"],
        past_projects=[{"name": "Project X", "year": 2024}],
        funding_history=[{"programme": "tubitak_1507", "amount_tl": 3_000_000}],
        preferred_languages=["tr", "en"],
    )
    assert profile.entity_type == "sme"
    assert profile.trl_current == 4 and profile.trl_target == 7
    assert profile.past_projects[0]["name"] == "Project X"


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
async def test_organization_routes_are_registered(
    unauthed_client: AsyncClient,
) -> None:
    """GET + PUT /organizations/profile are wired in and auth-gated."""

    get_resp = await unauthed_client.get("/api/v1/organizations/profile")
    assert get_resp.status_code in (401, 403)

    put_resp = await unauthed_client.put(
        "/api/v1/organizations/profile", json={"entity_type": "sme"}
    )
    assert put_resp.status_code in (401, 403)
