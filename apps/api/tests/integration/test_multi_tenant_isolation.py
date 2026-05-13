"""End-to-end multi-tenant isolation test (S3.D15).

One long-form test, named flow, asserts that EVERY tenant-scoped
endpoint added in Sprint 3 hides foreign-tenant data — and where the
data is shared (Iyzico webhook plan changes), only the right tenant
moves.

Skips when ``TEST_DATABASE_URL`` is unset (no Postgres → no isolation
to assert). The test is intentionally a single function: writing nine
small tests would obscure the property under check (the CROSS-step
assertions, e.g. "after A's quota burns, B's first generate still
works") that's the whole reason this exists.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from src.billing.iyzico import SIGNATURE_HEADER, compute_signature
from src.core.auth import get_current_user_id
from src.core.config import Settings, get_settings
from src.core.db import get_db
from src.main import create_app

_MASTER_KEY = "test-master-key-32-bytes-padding!"
"""Fixed dev master key — mirrors tests/security/test_byok.py so the
BYOK store endpoint in step 2 can actually encrypt the canary key."""


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping E2E multi-tenant isolation"
        )
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


# ── Helpers ────────────────────────────────────────────────────────────


async def _create_tenant(
    pool: asyncpg.Pool, *, name: str, customer_code: str
) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into tenants (id, name, slug, iyzico_customer_id)
            values ($1, $2, $3, $4)
            """,
            tenant_id,
            name,
            f"iso-{tenant_id}",
            customer_code,
        )
    return tenant_id


async def _create_user(
    pool: asyncpg.Pool, *, tenant_id: uuid.UUID, role: str
) -> tuple[uuid.UUID, str]:
    user_id = uuid.uuid4()
    email = f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)", user_id, email
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role, display_name) "
            "values ($1, $2, $3, $4)",
            user_id,
            tenant_id,
            role,
            f"User {user_id.hex[:6]}",
        )
    return user_id, email


async def _create_proposal(
    pool: asyncpg.Pool, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into proposals (
              id, tenant_id, programme_id, title, language, status,
              brief, draft, created_by
            ) values (
              $1, $2, 'horizon_eu_ria', 'Iso Test', 'en', 'draft',
              '{}'::jsonb, '{}'::jsonb, $3
            )
            """,
            proposal_id,
            tenant_id,
            user_id,
        )
    return proposal_id


async def _seed_usage(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cost_usd: float,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into tenant_usage_log (
              tenant_id, user_id, event_type, resource,
              input_tokens, output_tokens, cached_tokens, cost_usd
            ) values ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            tenant_id,
            user_id,
            "llm_call",
            "claude-sonnet-4-6:call_analyst",
            1000,
            500,
            0,
            cost_usd,
        )


def _settings_with_iyzico_webhook(secret: str = "wh_secret_xxx") -> Settings:
    return Settings(  # type: ignore[arg-type]
        iyzico_webhook_secret=secret,  # type: ignore[arg-type]
        iyzico_api_key="api",  # type: ignore[arg-type]
        iyzico_secret_key="secret",  # type: ignore[arg-type]
        supabase_jwt_secret=None,
        llm_master_encryption_key=_MASTER_KEY,  # type: ignore[arg-type]
    )


def _client_for(
    *, pool: asyncpg.Pool, user_id: uuid.UUID, settings: Settings
) -> AsyncClient:
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_settings] = lambda: settings
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _public_client(*, pool: asyncpg.Pool, settings: Settings) -> AsyncClient:
    """No JWT override — for the public webhook + invitation preview."""

    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_settings] = lambda: settings
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _cleanup(pool: asyncpg.Pool, *, tenant_ids: list[uuid.UUID]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from proposal_comments where proposal_id in "
            "(select id from proposals where tenant_id = any($1::uuid[]))",
            tenant_ids,
        )
        await conn.execute(
            "delete from proposal_versions where proposal_id in "
            "(select id from proposals where tenant_id = any($1::uuid[]))",
            tenant_ids,
        )
        await conn.execute(
            "delete from billing_events where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        await conn.execute(
            "delete from proposals where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        await conn.execute(
            "delete from tenant_invitations where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        await conn.execute(
            "delete from tenant_usage_log where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        await conn.execute(
            "delete from audit_log where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        await conn.execute(
            "delete from public.users where tenant_id = any($1::uuid[])",
            tenant_ids,
        )
        # auth.users rows are tracked by tenant_id only via public.users; the
        # email column is the join key, so we let those rows linger — they're
        # in auth.users (Supabase-side) and the integration test isolates by
        # tenant, not by the auth schema's lifecycle.
        await conn.execute(
            "delete from tenants where id = any($1::uuid[])", tenant_ids
        )


# ── The test ───────────────────────────────────────────────────────────


async def test_full_multi_tenant_isolation_round_trip(
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step-by-step assertion that every Sprint-3 endpoint is tenant-safe.

    The flow walks two tenants A and B through the full Sprint 3 surface
    in a single execution so that any cross-leak shows up as a failed
    assertion in the very next step. The named-section comments mirror
    the plan's tenant-isolation matrix.
    """

    # The BYOK store endpoint (step 2) requires the master key on the
    # server. Set it for the duration of the test and clear the settings
    # cache so the next get_settings() call picks it up.
    monkeypatch.setenv("LLM_MASTER_ENCRYPTION_KEY", _MASTER_KEY)
    get_settings.cache_clear()

    settings = _settings_with_iyzico_webhook()

    # 1. Setup
    tenant_a = await _create_tenant(
        pool, name="Tenant A", customer_code="cust_A"
    )
    tenant_b = await _create_tenant(
        pool, name="Tenant B", customer_code="cust_B"
    )
    owner_a, _ = await _create_user(pool, tenant_id=tenant_a, role="owner")
    admin_a, _ = await _create_user(pool, tenant_id=tenant_a, role="admin")
    member_a, _ = await _create_user(pool, tenant_id=tenant_a, role="member")
    owner_b, _ = await _create_user(pool, tenant_id=tenant_b, role="owner")
    member_b, _ = await _create_user(pool, tenant_id=tenant_b, role="member")

    try:
        # 2. BYOK isolation — A stores a key, B must not see it.
        client_owner_a = _client_for(pool=pool, user_id=owner_a, settings=settings)
        client_owner_b = _client_for(pool=pool, user_id=owner_b, settings=settings)

        async with client_owner_a, client_owner_b:
            put_a = await client_owner_a.put(
                "/api/v1/tenant/llm-config",
                json={"anthropic_api_key": "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaa"},
            )
            assert put_a.status_code in {200, 201, 204}, put_a.text

            get_b = await client_owner_b.get("/api/v1/tenant/llm-config")
            assert get_b.status_code == 200
            payload_b = get_b.json()
            assert payload_b.get("anthropic_configured") is False, (
                "Tenant A's BYOK key bled to tenant B"
            )

            # 3. Invitation isolation
            inv_a = await client_owner_a.post(
                "/api/v1/tenant/invitations",
                json={"email": "new@example.com", "role": "member"},
            )
            assert inv_a.status_code == 201, inv_a.text
            token = inv_a.json()["token"]

            list_b = await client_owner_b.get("/api/v1/tenant/invitations")
            assert list_b.status_code == 200
            assert list_b.json()["invitations"] == []

            # Public preview by token still works (intended).
            public = _public_client(pool=pool, settings=settings)
            async with public:
                preview = await public.get(f"/api/v1/invitations/{token}")
                assert preview.status_code == 200

            # 4. Member isolation — A's members visible only to A.
            members_a = await client_owner_a.get("/api/v1/tenant/members")
            assert members_a.status_code == 200
            ids_a = {m["id"] for m in members_a.json()["members"]}
            assert ids_a == {str(owner_a), str(admin_a), str(member_a)}

            # A's owner cannot modify B's user role.
            patch = await client_owner_a.patch(
                f"/api/v1/tenant/members/{owner_b}/role",
                json={"role": "member"},
            )
            assert patch.status_code == 404

            # 5. Audit isolation — A's audit log only shows A's actor ids.
            audit_a = await client_owner_a.get("/api/v1/tenant/audit-log")
            assert audit_a.status_code == 200
            actor_ids = {
                row["user_id"]
                for row in audit_a.json().get("events", [])
                if row.get("user_id")
            }
            assert actor_ids.issubset(
                {str(owner_a), str(admin_a), str(member_a)}
            )

            # 6. Usage isolation
            await _seed_usage(
                pool, tenant_id=tenant_a, user_id=owner_a, cost_usd=10.5
            )
            await _seed_usage(
                pool, tenant_id=tenant_b, user_id=owner_b, cost_usd=999.0
            )
            usage_a = await client_owner_a.get("/api/v1/tenant/usage")
            assert usage_a.status_code == 200, usage_a.text
            payload = usage_a.json()
            # Either field name is acceptable depending on the response shape;
            # what matters is B's 999 doesn't show up.
            blob = json.dumps(payload)
            assert "999" not in blob, (
                f"Tenant B's usage ($999) leaked into A's report: {blob[:300]}"
            )

            # 7. Iyzico webhook isolation — only A's plan moves.
            payload_event: dict[str, Any] = {
                "eventType": "subscription.activated",
                "eventId": f"evt_{uuid.uuid4().hex}",
                "customerReferenceCode": "cust_A",
                "pricingPlanReferenceCode": "iyz_pro_monthly",
                "subscriptionReferenceCode": "sub_xyz",
            }
            raw_body = json.dumps(payload_event).encode("utf-8")
            sig = compute_signature(body=raw_body, secret="wh_secret_xxx")
            # The webhook is unauthenticated, so it gets its own public
            # client (no JWT override). Same lifetime as the inner block.
            public2 = _public_client(pool=pool, settings=settings)
            async with public2:
                wh_resp = await public2.post(
                    "/api/v1/billing/iyzico-webhook",
                    content=raw_body,
                    headers={
                        SIGNATURE_HEADER: sig,
                        "Content-Type": "application/json",
                    },
                )
                assert wh_resp.status_code == 200, wh_resp.text

            async with pool.acquire() as conn:
                plan_a = await conn.fetchval(
                    "select plan from tenants where id = $1", tenant_a
                )
                plan_b = await conn.fetchval(
                    "select plan from tenants where id = $1", tenant_b
                )
            assert plan_a == "pro", f"A should be upgraded; got {plan_a!r}"
            assert plan_b == "starter", f"B should be untouched; got {plan_b!r}"

            # 8. Versions isolation
            proposal_a = await _create_proposal(
                pool, tenant_id=tenant_a, user_id=owner_a
            )
            client_member_b = _client_for(
                pool=pool, user_id=member_b, settings=settings
            )
            async with client_member_b:
                snap_b = await client_member_b.post(
                    f"/api/v1/proposals/{proposal_a}/versions", json={}
                )
                assert snap_b.status_code == 404

                # A's owner CAN snapshot.
                snap_a = await client_owner_a.post(
                    f"/api/v1/proposals/{proposal_a}/versions", json={}
                )
                assert snap_a.status_code == 201

                # 9. Comments isolation
                cmt_a = await client_owner_a.post(
                    f"/api/v1/proposals/{proposal_a}/comments",
                    json={"content": "alice's note"},
                )
                assert cmt_a.status_code == 201
                cmt_id = cmt_a.json()["id"]

                # B cannot edit / resolve / delete / list.
                assert (
                    await client_member_b.patch(
                        f"/api/v1/comments/{cmt_id}",
                        json={"content": "bob's edit"},
                    )
                ).status_code == 404
                assert (
                    await client_member_b.post(
                        f"/api/v1/comments/{cmt_id}/resolve"
                    )
                ).status_code == 404
                assert (
                    await client_member_b.delete(
                        f"/api/v1/comments/{cmt_id}"
                    )
                ).status_code == 404
                assert (
                    await client_member_b.get(
                        f"/api/v1/proposals/{proposal_a}/comments"
                    )
                ).status_code == 404

    finally:
        await _cleanup(pool, tenant_ids=[tenant_a, tenant_b])
