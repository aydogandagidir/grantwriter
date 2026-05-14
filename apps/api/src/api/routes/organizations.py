"""Organization-profile endpoints.

One profile per tenant — drives the EligibilityChecker (entity type vs
call eligibility tags, country vs geo scope, TRL fit) and serves as the
priors input to IdeaGenerator.

V1 surface is a structured form: ``GET`` reads the profile (or 404 when
the tenant hasn't filled one in yet), ``PUT`` upserts it. The
website-URL Smart Inference path (``POST /infer-profile`` →
OrgProfileExtractor agent) lands in a Faz 2 follow-up.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.core.auth import CurrentUserId
from src.core.db import get_db
from src.core.tenant import resolve_tenant_and_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])

EntityType = Literal[
    "individual", "sme", "university", "large_corp", "ngo", "research_org"
]


# ── Models ─────────────────────────────────────────────────────────────


class OrganizationProfileUpsert(BaseModel):
    """Body for ``PUT /api/v1/organizations/profile``.

    Every field is optional — the form is filled progressively. An
    empty body is a valid request (creates a near-empty profile row),
    though in practice the FE sends at least entity_type + country.
    """

    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(default=None, max_length=300)
    entity_type: EntityType | None = None
    country: str | None = Field(default=None, max_length=8)
    nuts_region: str | None = Field(default=None, max_length=16)
    nace_codes: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    team_size: int | None = Field(default=None, ge=0)
    annual_revenue_eur: float | None = Field(default=None, ge=0)
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    technology_areas: list[str] = Field(default_factory=list)
    trl_current: int | None = Field(default=None, ge=1, le=9)
    trl_target: int | None = Field(default=None, ge=1, le=9)
    expertise_keywords: list[str] = Field(default_factory=list)
    past_projects: list[dict[str, Any]] = Field(default_factory=list)
    funding_history: list[dict[str, Any]] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=list)


class OrganizationProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    legal_name: str | None
    entity_type: EntityType | None
    country: str | None
    nuts_region: str | None
    nace_codes: list[str]
    sectors: list[str]
    team_size: int | None
    annual_revenue_eur: float | None
    founded_year: int | None
    technology_areas: list[str]
    trl_current: int | None
    trl_target: int | None
    expertise_keywords: list[str]
    past_projects: list[dict[str, Any]]
    funding_history: list[dict[str, Any]]
    preferred_languages: list[str]
    created_at: datetime
    updated_at: datetime


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "/profile",
    response_model=OrganizationProfile,
    summary="Read the caller's organization profile",
)
async def get_organization_profile(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> OrganizationProfile:
    """404 when the tenant hasn't created a profile yet — the FE treats
    that as 'show the empty onboarding form'."""

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)
    row = await conn.fetchrow(
        """
        SELECT tenant_id, legal_name, entity_type, country, nuts_region,
               nace_codes, sectors, team_size, annual_revenue_eur,
               founded_year, technology_areas, trl_current, trl_target,
               expertise_keywords, past_projects, funding_history,
               preferred_languages, created_at, updated_at
          FROM organization_profiles
         WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="organization profile not set for this tenant",
        )
    return _row_to_profile(row)


@router.put(
    "/profile",
    response_model=OrganizationProfile,
    summary="Create or replace the caller's organization profile",
)
async def upsert_organization_profile(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: OrganizationProfileUpsert,
) -> OrganizationProfile:
    """Idempotent upsert keyed on ``tenant_id`` (the table's PK).

    Full replacement rather than partial PATCH — the FE always sends
    the complete form state, and a replace keeps the round-trip simple.
    Embedding is left NULL in V1; EligibilityChecker (Faz 2.6) uses the
    structured fields directly, and the embedding column fills in when
    we add semantic org-to-call matching.
    """

    if (
        body.trl_current is not None
        and body.trl_target is not None
        and body.trl_target < body.trl_current
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="trl_target must be >= trl_current",
        )

    tenant_id, _ = await resolve_tenant_and_role(conn, user_id=user_id)

    row = await conn.fetchrow(
        """
        INSERT INTO organization_profiles (
          tenant_id, legal_name, entity_type, country, nuts_region,
          nace_codes, sectors, team_size, annual_revenue_eur,
          founded_year, technology_areas, trl_current, trl_target,
          expertise_keywords, past_projects, funding_history,
          preferred_languages, updated_at
        ) VALUES (
          $1, $2, $3, $4, $5,
          $6::text[], $7::text[], $8, $9,
          $10, $11::text[], $12, $13,
          $14::text[], $15::jsonb, $16::jsonb,
          $17::text[], now()
        )
        ON CONFLICT (tenant_id) DO UPDATE SET
          legal_name = EXCLUDED.legal_name,
          entity_type = EXCLUDED.entity_type,
          country = EXCLUDED.country,
          nuts_region = EXCLUDED.nuts_region,
          nace_codes = EXCLUDED.nace_codes,
          sectors = EXCLUDED.sectors,
          team_size = EXCLUDED.team_size,
          annual_revenue_eur = EXCLUDED.annual_revenue_eur,
          founded_year = EXCLUDED.founded_year,
          technology_areas = EXCLUDED.technology_areas,
          trl_current = EXCLUDED.trl_current,
          trl_target = EXCLUDED.trl_target,
          expertise_keywords = EXCLUDED.expertise_keywords,
          past_projects = EXCLUDED.past_projects,
          funding_history = EXCLUDED.funding_history,
          preferred_languages = EXCLUDED.preferred_languages,
          updated_at = now()
        RETURNING tenant_id, legal_name, entity_type, country, nuts_region,
                  nace_codes, sectors, team_size, annual_revenue_eur,
                  founded_year, technology_areas, trl_current, trl_target,
                  expertise_keywords, past_projects, funding_history,
                  preferred_languages, created_at, updated_at
        """,
        tenant_id,
        body.legal_name,
        body.entity_type,
        body.country.lower() if body.country else None,
        body.nuts_region,
        list(body.nace_codes),
        list(body.sectors),
        body.team_size,
        body.annual_revenue_eur,
        body.founded_year,
        list(body.technology_areas),
        body.trl_current,
        body.trl_target,
        list(body.expertise_keywords),
        json.dumps(body.past_projects),
        json.dumps(body.funding_history),
        list(body.preferred_languages),
    )
    assert row is not None
    logger.info(
        "organization_profile_upserted",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "entity_type": body.entity_type,
        },
    )
    return _row_to_profile(row)


# ── Helpers ────────────────────────────────────────────────────────────


def _row_to_profile(row: asyncpg.Record) -> OrganizationProfile:
    return OrganizationProfile(
        tenant_id=UUID(str(row["tenant_id"])),
        legal_name=row["legal_name"],
        entity_type=row["entity_type"],
        country=row["country"],
        nuts_region=row["nuts_region"],
        nace_codes=list(row["nace_codes"] or []),
        sectors=list(row["sectors"] or []),
        team_size=row["team_size"],
        annual_revenue_eur=(
            float(row["annual_revenue_eur"])
            if row["annual_revenue_eur"] is not None
            else None
        ),
        founded_year=row["founded_year"],
        technology_areas=list(row["technology_areas"] or []),
        trl_current=row["trl_current"],
        trl_target=row["trl_target"],
        expertise_keywords=list(row["expertise_keywords"] or []),
        past_projects=_jsonb_list(row["past_projects"]),
        funding_history=_jsonb_list(row["funding_history"]),
        preferred_languages=list(row["preferred_languages"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _jsonb_list(value: Any) -> list[dict[str, Any]]:
    """asyncpg returns jsonb as a parsed object or a string depending on
    codec registration — coerce both into a list of dicts."""

    if value is None:
        return []
    parsed = json.loads(value) if isinstance(value, str) else value
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


__all__ = [
    "EntityType",
    "OrganizationProfile",
    "OrganizationProfileUpsert",
    "router",
]
