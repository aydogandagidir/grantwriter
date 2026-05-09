"""DB-backed tests: cost_tracker writes and key_vault round-trips.

Skip when ``TEST_DATABASE_URL`` is unset (matches the security suite). The
expected fixture is a fresh pgvector container with all migrations through
``20260508130200_rls_policies.sql`` applied + ``auth_stub.sql`` loaded.
"""

from __future__ import annotations

import json

import asyncpg
from src.llm import cost_tracker, key_vault
from src.llm.router import LLMRouter, PlatformKeys

from tests.llm.conftest import (
    FakeProvider,
    make_request,
    make_response,
    transient_tenant,
)

_MASTER_KEY = "test-master-key-32-bytes-padding!!"


# ── cost_tracker ────────────────────────────────────────────────────────


async def test_log_call_writes_tenant_usage_log_row(pool: asyncpg.Pool) -> None:
    async with transient_tenant(pool) as tenant_id:
        request = make_request(task="excellence_writer", tenant_id=tenant_id)
        response = make_response(
            text="ok",
            model="claude-opus-4-7",
            provider="claude",
            input_tokens=120,
            output_tokens=80,
            cached_tokens=200,
            cache_creation_tokens=50,
            cost_usd=0.0042,
        )

        async with pool.acquire() as conn:
            row_id = await cost_tracker.log_call(conn, request=request, response=response)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("select * from tenant_usage_log where id = $1", row_id)

        assert row is not None
        assert row["tenant_id"] == tenant_id
        assert row["event_type"] == "llm_call"
        assert row["resource"] == "claude-opus-4-7"
        assert row["input_tokens"] == 120
        assert row["output_tokens"] == 80
        assert row["cached_tokens"] == 200
        assert float(row["cost_usd"]) == 0.0042
        assert row["used_byok"] is False

        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        assert meta["task"] == "excellence_writer"
        assert meta["provider"] == "claude"
        assert meta["cache_creation_tokens"] == 50


async def test_router_logs_each_successful_call(pool: asyncpg.Pool) -> None:
    """Router._log fires once per successful complete()."""

    async with transient_tenant(pool) as tenant_id:
        primary = FakeProvider(
            "claude",
            [
                make_response(
                    text="hi",
                    model="claude-opus-4-7",
                    provider="claude",
                    cost_usd=0.0015,
                )
            ],
        )
        router = LLMRouter(
            providers={"claude": primary, "openai": FakeProvider("openai", [])},
            platform_keys=PlatformKeys(anthropic="x", openai="y"),
            master_encryption_key=None,
            db_pool=pool,
        )

        await router.complete(make_request(task="call_analyst", tenant_id=tenant_id))

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "select count(*) from tenant_usage_log where tenant_id = $1",
                tenant_id,
            )
        assert count == 1


# ── key_vault round-trip ────────────────────────────────────────────────


async def test_byok_round_trip(pool: asyncpg.Pool) -> None:
    """Encrypt → decrypt — plaintext recovers exactly."""

    plaintext = "sk-ant-DO-NOT-LOG-test-byok-key-1234"

    async with transient_tenant(pool) as tenant_id:
        async with pool.acquire() as conn:
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                plaintext_key=plaintext,
                master_key=_MASTER_KEY,
            )
            recovered = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                master_key=_MASTER_KEY,
            )
        assert recovered == plaintext


async def test_byok_wrong_master_key_fails(pool: asyncpg.Pool) -> None:
    """Decrypting with the wrong master key raises rather than returning garbage."""

    import pytest

    async with (
        transient_tenant(pool) as tenant_id,
        pool.acquire() as conn,
    ):
        await key_vault.store_byok_key(
            conn,
            tenant_id=tenant_id,
            kind="openai",
            plaintext_key="sk-openai-test",
            master_key=_MASTER_KEY,
        )
        with pytest.raises(asyncpg.PostgresError):
            await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="openai",
                master_key="WRONG-MASTER-KEY-32-bytes-padding",
            )


async def test_byok_get_returns_none_when_unset(pool: asyncpg.Pool) -> None:
    async with transient_tenant(pool) as tenant_id:
        async with pool.acquire() as conn:
            recovered = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                master_key=_MASTER_KEY,
            )
        assert recovered is None


async def test_prefer_managed_keys_default_true(pool: asyncpg.Pool) -> None:
    async with (
        transient_tenant(pool) as tenant_id,
        pool.acquire() as conn,
    ):
        assert await key_vault.prefer_managed_keys(conn, tenant_id=tenant_id) is True


async def test_router_uses_byok_when_tenant_opted_in(pool: asyncpg.Pool) -> None:
    """End-to-end: tenant has a stored BYOK key + opted out of managed →
    the router resolves to the BYOK plaintext (logged with used_byok=True)."""

    byok_plaintext = "sk-ant-byok-end-to-end-test-key"

    captured_keys: list[str] = []

    def capture_response(_req: object, _model: str, api_key: str) -> object:
        captured_keys.append(api_key)
        return make_response(model="claude-opus-4-7", provider="claude", cost_usd=0.001)

    primary = FakeProvider("claude", [capture_response])

    async with transient_tenant(pool) as tenant_id:
        async with pool.acquire() as conn:
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                plaintext_key=byok_plaintext,
                master_key=_MASTER_KEY,
            )
            await conn.execute(
                "update tenant_llm_config set use_managed_keys = false where tenant_id = $1",
                tenant_id,
            )

        router = LLMRouter(
            providers={"claude": primary, "openai": FakeProvider("openai", [])},
            platform_keys=PlatformKeys(anthropic="platform-fallback", openai=None),
            master_encryption_key=_MASTER_KEY,
            db_pool=pool,
        )

        response = await router.complete(make_request(task="call_analyst", tenant_id=tenant_id))

        assert response.used_byok is True
        # The provider received the decrypted BYOK key, not the platform key.
        assert captured_keys == [byok_plaintext]

        async with pool.acquire() as conn:
            logged_used_byok = await conn.fetchval(
                "select used_byok from tenant_usage_log where tenant_id = $1",
                tenant_id,
            )
        assert logged_used_byok is True
